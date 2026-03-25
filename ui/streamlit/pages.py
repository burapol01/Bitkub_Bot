from __future__ import annotations

from collections import defaultdict
from typing import Any

import streamlit as st

from clients.bitkub_client import get_market_symbols_v3

from config import CONFIG_PATH, reload_config, save_config, summarize_config_changes
from services.account_service import build_live_holdings_snapshot
from services.db_service import (
    DB_PATH,
    fetch_dashboard_summary,
    fetch_db_maintenance_summary,
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_recent_telegram_command_log,
    fetch_recent_telegram_outbox,
    fetch_reporting_summary,
    fetch_runtime_event_log,
    insert_runtime_event,
)
from services.execution_service import (
    LiveExecutionGuardrailError,
    build_live_execution_guardrails,
    cancel_live_order,
    refresh_live_order_from_exchange,
)
from services.reconciliation_service import (
    extract_available_balances,
    summarize_live_reconciliation,
)

from services.strategy_lab_service import (
    build_coin_ranking,
    fetch_market_snapshot_coverage,
    fetch_trade_analytics,
    run_market_candle_replay,
    run_market_snapshot_replay,
    sync_candles_for_symbols,
)
from ui.streamlit.actions import (
    persist_execution_order_update,
    submit_manual_order_from_ui,
)
from services.telegram_service import (
    DEFAULT_TELEGRAM_NOTIFY_EVENTS,
    telegram_settings_snapshot,
)
from ui.streamlit.data import calc_daily_totals
from ui.streamlit.refresh import PAGE_ORDER, render_refreshable_fragment
from ui.streamlit.styles import badge, render_metric_card
from utils.time_utils import now_text


def _show_config_save_feedback() -> None:
    summary_lines = st.session_state.get("config_save_summary")
    summary_title = st.session_state.get("config_save_title")
    if not summary_lines:
        return

    st.success(summary_title or "Saved config.json")
    with st.expander("Applied Config Changes", expanded=True):
        for line in summary_lines:
            st.write(f"- {line}")


def _save_config_with_feedback(
    current_config: dict[str, Any],
    updated_config: dict[str, Any],
    success_title: str,
) -> bool:
    _, errors = save_config(updated_config)
    if errors:
        for error in errors:
            st.error(error)
        return False

    st.session_state["config_save_title"] = success_title
    st.session_state["config_save_summary"] = summarize_config_changes(
        current_config,
        updated_config,
    )
    return True


def _set_live_ops_feedback(title: str, lines: list[str], tone: str = "success") -> None:
    st.session_state["live_ops_feedback"] = {
        "title": title,
        "lines": lines,
        "tone": tone,
    }


def _show_live_ops_feedback() -> None:
    payload = st.session_state.get("live_ops_feedback")
    if not payload:
        return

    title = str(payload.get("title", "Live Ops result"))
    lines = [str(line) for line in payload.get("lines", [])]
    tone = str(payload.get("tone", "success"))

    if tone == "error":
        st.error(title)
    elif tone == "warning":
        st.warning(title)
    else:
        st.success(title)

    for line in lines:
        st.caption(line)


def _normalize_market_symbol(raw_symbol: Any) -> str | None:
    value = str(raw_symbol or "").strip().upper().replace("-", "_")
    if not value:
        return None
    parts = value.split("_")
    if len(parts) != 2:
        return value
    left, right = parts
    if left == "THB":
        return f"THB_{right}"
    if right == "THB":
        return f"THB_{left}"
    return value


def _summarize_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, int] = defaultdict(int)
    for line in lines:
        grouped[str(line)] += 1
    return [
        {"message": message, "count": count}
        for message, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    ]


def _classify_runtime_event(row: dict[str, Any]) -> dict[str, Any]:
    event_type = str(row.get("event_type") or "")
    severity = str(row.get("severity") or "")
    message = str(row.get("message") or "")
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    normalized = f"{event_type} {message}".lower()

    category = "General"
    topic = event_type or "event"
    hint = "Review the message and related runtime context."

    if "runtime_state" in normalized or "access is denied" in normalized:
        category = "State File"
        topic = "runtime_state.json write lock"
        hint = "Close any editor/preview that may lock runtime_state.json, then restart the engine."
    elif "endpoint not found for path /api/market/my-open-orders" in normalized or "error=61" in normalized:
        category = "Unsupported Symbol"
        topic = "broker coin or unsupported order endpoint"
        hint = "Do not rely on open_orders/order_history for this symbol. Remove it from live shortlist or mark it unsupported."
    elif event_type == "telegram_delivery" or "telegram" in normalized:
        category = "Runtime"
        topic = "telegram delivery"
        hint = "Check TELEGRAM_BOT_TOKEN plus TELEGRAM_CHAT_IDS / TELEGRAM_CHAT_ID / TELEGRAM_ALLOWED_CHAT_IDS in .env and review telegram logs."
    elif event_type in {"live_order_cancel", "auto_live_exit", "auto_live_entry", "auto_live_entry_review", "execution_reconciliation"} or "execution" in normalized or "live order" in normalized:
        category = "Execution"
        topic = event_type or "execution"
        hint = "Check order state, exchange_order_id, and open orders before retrying the action."
    elif event_type in {"private_api_status", "account_snapshot"} or "bitkub api" in normalized or "wallet/" in normalized:
        category = "Private API"
        topic = event_type or "private_api"
        hint = "Verify credentials, permissions, and whether the endpoint is supported for the target symbol."
    elif "config" in normalized or event_type == "trading_mode":
        category = "Config / Mode"
        topic = event_type or "config"
        hint = "Check config.json values and reload the engine after saving changes."
    elif severity == "error":
        category = "Runtime"
        topic = event_type or "runtime_error"
        hint = "Inspect the stack-facing message and latest runtime events around this timestamp."

    return {
        **row,
        "category": category,
        "topic": topic,
        "hint": hint,
        "details": details,
    }


def _current_private_api_issues(private_ctx: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for error in private_ctx.get("errors", []):
        message = str(error)
        category = "Private API"
        topic = "snapshot error"
        hint = "Verify private API access and endpoint support."
        if "Endpoint not found for path /api/market/my-open-orders" in message:
            category = "Unsupported Symbol"
            topic = "open_orders unsupported"
            hint = "This symbol likely belongs to a broker-coin group or unsupported order endpoint path."
        rows.append(
            {
                "created_at": "current",
                "event_type": "account_snapshot",
                "severity": "warning",
                "message": message,
                "category": category,
                "topic": topic,
                "hint": hint,
            }
        )

    snapshot = private_ctx.get("account_snapshot")
    open_orders = snapshot.get("open_orders", {}) if isinstance(snapshot, dict) else {}
    if isinstance(open_orders, dict):
        for symbol, entry in sorted(open_orders.items()):
            if not isinstance(entry, dict) or entry.get("ok", False):
                continue
            error = str(entry.get("error") or "")
            if not error:
                continue
            category = "Private API"
            topic = f"open_orders[{symbol}]"
            hint = "Review endpoint support and open-order capability for this symbol."
            if "Endpoint not found for path /api/market/my-open-orders" in error:
                category = "Unsupported Symbol"
                topic = f"open_orders[{symbol}] unsupported"
                hint = "This symbol likely cannot use the standard my-open-orders endpoint."
            rows.append(
                {
                    "created_at": "current",
                    "event_type": "open_orders",
                    "severity": "warning",
                    "message": f"{symbol}: {error}",
                    "category": category,
                    "topic": topic,
                    "hint": hint,
                }
            )
    return rows


@st.cache_data(ttl=900, show_spinner=False)
def _fetch_market_symbol_universe() -> dict[str, Any]:
    try:
        payload = get_market_symbols_v3()
        symbols: list[str] = []
        for row in payload:
            if isinstance(row, dict):
                raw_symbol = row.get("symbol") or row.get("id") or row.get("name")
            else:
                raw_symbol = row
            normalized = _normalize_market_symbol(raw_symbol)
            if normalized and normalized not in symbols:
                symbols.append(normalized)
        symbols.sort()
        return {"symbols": symbols, "error": None}
    except Exception as e:
        return {"symbols": [], "error": str(e)}


def _build_rule_seed(config: dict[str, Any], symbol: str) -> dict[str, Any]:
    existing = config["rules"].get(symbol)
    if existing:
        return dict(existing)
    return {
        "buy_below": 1.0,
        "sell_above": 1.1,
        "budget_thb": 100.0,
        "stop_loss_percent": 1.0,
        "take_profit_percent": 1.2,
        "max_trades_per_day": 1,
    }


def render_sidebar(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any],
    selected_page: str,
) -> str:
    with st.sidebar:
        st.markdown("### Navigation")
        page_name = st.radio(
            "Page",
            PAGE_ORDER,
            index=PAGE_ORDER.index(selected_page),
            label_visibility="collapsed",
        )
        st.markdown("### Control")
        st.caption(f"Config: `{CONFIG_PATH}`")
        st.caption(f"SQLite: `{DB_PATH}`")
        if st.button("Refresh Dashboard", use_container_width=True):
            st.rerun()
        st.markdown("### Status")
        st.markdown(badge(f"Mode {str(config['mode']).upper()}", "info"), unsafe_allow_html=True)
        for item in private_ctx["private_api_capabilities"]:
            tone = "good" if item.endswith("=OK") else "warn" if item.endswith("=PARTIAL") else "bad"
            st.markdown(badge(item, tone), unsafe_allow_html=True)
        st.caption(private_ctx["private_api_status"])
    return page_name


def render_overview_page(
    *,
    config: dict[str, Any],
    runtime: dict[str, Any],
    ticker_rows: list[dict[str, Any]],
    private_ctx: dict[str, Any],
    today: str,
) -> None:
    total_trades, total_wins, total_losses, total_pnl = calc_daily_totals(runtime["daily_stats"])
    dashboard_summary = fetch_dashboard_summary(today=today)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_metric_card("Trading Mode", str(config["mode"]).upper(), private_ctx["private_api_status"])
    with col2:
        render_metric_card("Rules", str(len(config["rules"])), f"Open paper positions {len(runtime['positions'])}")
    with col3:
        render_metric_card(
            "Tracked Today",
            str(len(runtime["daily_stats"].get(today, {}))),
            f"Cooldowns {len(runtime['cooldowns'])}",
        )
    with col4:
        render_metric_card("Realized Today", f"{total_pnl:,.2f} THB", f"Trades {total_trades} | W {total_wins} / L {total_losses}")

    st.markdown('<div class="panel-title">Market Overview</div>', unsafe_allow_html=True)
    st.dataframe(
        [
            {
                "symbol": row["symbol"],
                "last": f"{row['last_price']:,.8f}" if row["last_price"] is not None else "n/a",
                "buy_below": f"{row['buy_below']:,.8f}",
                "sell_above": f"{row['sell_above']:,.8f}",
                "zone": row["zone"],
            }
            for row in ticker_rows
        ],
        use_container_width=True,
        hide_index=True,
    )

    status_left, status_right = st.columns([1.05, 0.95])
    with status_left:
        st.markdown('<div class="panel-title">Control Snapshot</div>', unsafe_allow_html=True)
        pause_badges = [
            badge("Manual Pause ON", "warn" if runtime["manual_pause"] else "good"),
            badge(
                "Private API Ready" if private_ctx["account_snapshot"] is not None else "Private API Unavailable",
                "good" if private_ctx["account_snapshot"] is not None else "bad",
            ),
        ]
        st.markdown(" ".join(pause_badges), unsafe_allow_html=True)
        st.markdown(
            " ".join(
                badge(item, "good" if item.endswith("=OK") else "warn" if item.endswith("=PARTIAL") else "bad")
                for item in private_ctx["private_api_capabilities"]
            ),
            unsafe_allow_html=True,
        )
        st.caption(private_ctx["private_api_status"])
    with status_right:
        st.markdown('<div class="panel-title">Latest Execution</div>', unsafe_allow_html=True)
        latest_execution = dashboard_summary.get("latest_execution_order")
        if latest_execution:
            st.write(
                f"{latest_execution['symbol']} | {latest_execution['side']} | {latest_execution['state']}"
            )
            st.caption(
                f"id={latest_execution['id']} updated={latest_execution['updated_at']}"
            )
            if latest_execution.get("message"):
                st.caption(str(latest_execution["message"]))
        else:
            st.caption("No execution orders stored yet.")

    if runtime["messages"]:
        st.markdown(
            '<div class="note-strip"><strong>Runtime Restore</strong><br>' + "<br>".join(runtime["messages"]) + "</div>",
            unsafe_allow_html=True,
        )


def render_account_page(
    *,
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
) -> None:
    account_snapshot = private_ctx["account_snapshot"]
    if account_snapshot is None:
        st.warning("Private API credentials are not configured or account snapshot is unavailable.")
        return

    st.markdown('<div class="panel-title">Capability Matrix</div>', unsafe_allow_html=True)
    st.markdown(
        " ".join(
            badge(item, "good" if item.endswith("=OK") else "warn" if item.endswith("=PARTIAL") else "bad")
            for item in private_ctx["private_api_capabilities"]
        ),
        unsafe_allow_html=True,
    )
    if private_ctx["errors"]:
        for error in private_ctx["errors"]:
            st.warning(error)

    holdings = build_live_holdings_snapshot(
        account_snapshot=account_snapshot,
        latest_prices=latest_prices,
        latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
    )
    total_holdings_value = sum(float(row.get("market_value_thb", 0.0)) for row in holdings)
    reserved_rows = sum(
        1 for row in holdings if float(row.get("reserved_qty", 0.0)) > 0
    )

    open_orders = account_snapshot.get("open_orders", {})
    open_rows: list[dict[str, Any]] = []
    if isinstance(open_orders, dict):
        for symbol, entry in sorted(open_orders.items()):
            if not isinstance(entry, dict):
                continue
            if entry.get("ok", False):
                payload = entry.get("data", {})
                rows = payload.get("result", payload) if isinstance(payload, dict) else payload
                count = len(rows) if isinstance(rows, list) else 0
                open_rows.append({"symbol": symbol, "status": "OK", "open_orders": count})
            else:
                error_message = str(entry.get("error") or "")
                if "Endpoint not found for path /api/market/my-open-orders" in error_message or "Endpoint not found for path /api/v3/market/my-open-orders" in error_message:
                    open_rows.append({"symbol": symbol, "status": "UNSUPPORTED", "open_orders": "n/a"})
                else:
                    open_rows.append({"symbol": symbol, "status": "ERROR", "open_orders": error_message})

    metric1, metric2, metric3 = st.columns(3)
    with metric1:
        render_metric_card("Holdings Rows", str(len(holdings)), f"Reserved rows {reserved_rows}")
    with metric2:
        render_metric_card("Total Holding Value", f"{total_holdings_value:,.2f} THB", "Mark-to-market estimate")
    with metric3:
        render_metric_card("Open Order Symbols", str(len(open_rows)), "Exchange snapshot summary")

    left, right = st.columns([1.15, 0.85])
    with left:
        st.markdown('<div class="panel-title">Live Holdings</div>', unsafe_allow_html=True)
        if holdings:
            st.dataframe(holdings, use_container_width=True, hide_index=True)
        else:
            st.caption("No live holdings found in the current account snapshot.")
    with right:
        st.markdown('<div class="panel-title">Exchange Open Orders</div>', unsafe_allow_html=True)
        if open_rows:
            st.dataframe(open_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No open-order rows were returned by the exchange snapshot.")


def render_live_ops_page(
    *,
    config: dict[str, Any],
    runtime: dict[str, Any],
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
    auto_refresh_run_every: str | None = None,
) -> None:
    client = private_ctx["client"]
    account_snapshot = private_ctx["account_snapshot"]
    if client is None or account_snapshot is None:
        st.warning("Private API is required for live operations.")
        return

    available_balances = extract_available_balances(account_snapshot)
    thb_available = float(available_balances.get("THB", 0.0))
    symbols = sorted(config["rules"].keys())
    manual_defaults = dict(config.get("live_manual_order", {}))
    default_symbol = str(manual_defaults.get("symbol", symbols[0]))

    def _load_live_ops_dynamic() -> dict[str, Any]:
        _, _, _, total_pnl = calc_daily_totals(runtime["daily_stats"])
        guardrails = build_live_execution_guardrails(
            config=config,
            trading_mode=str(config["mode"]),
            private_client=client,
            private_api_capabilities=private_ctx["private_api_capabilities"],
            manual_pause=runtime["manual_pause"],
            safety_pause=False,
            total_realized_pnl_thb=total_pnl,
            available_balances=extract_available_balances(account_snapshot),
            strategy_execution_wired=False,
        )
        execution_summary = fetch_execution_console_summary()
        return {
            "guardrails": guardrails,
            "execution_summary": execution_summary,
            "open_execution_orders": execution_summary["open_orders"],
            "recent_execution_orders": execution_summary["recent_orders"],
            "recent_execution_events": execution_summary["recent_events"],
        }

    def _render_live_ops_dynamic_top() -> None:
        dynamic = _load_live_ops_dynamic()
        guardrails = dynamic["guardrails"]
        open_execution_orders = dynamic["open_execution_orders"]
        recent_execution_orders = dynamic["recent_execution_orders"]
        recent_execution_events = dynamic["recent_execution_events"]
        last_execution = recent_execution_orders[0] if recent_execution_orders else None

        card1, card2, card3 = st.columns(3)
        with card1:
            render_metric_card(
                "Execution Ready",
                "YES" if guardrails.get("ready") else "NO",
                "Kill switch ON" if guardrails.get("live_execution_enabled") else "Kill switch OFF",
            )
        with card2:
            render_metric_card(
                "Open Live Orders",
                str(len(open_execution_orders)),
                f"Recent events {len(recent_execution_events)}",
            )
        with card3:
            render_metric_card(
                "THB Available",
                f"{float(extract_available_balances(account_snapshot).get('THB', 0.0)):,.2f}",
                f"Min balance {float(config['live_min_thb_balance']):,.2f}",
            )

        st.markdown('<div class="panel-title">Guardrail Snapshot</div>', unsafe_allow_html=True)
        if guardrails.get("blocked_reasons"):
            st.markdown(
                " ".join(badge(reason, "warn") for reason in guardrails["blocked_reasons"]),
                unsafe_allow_html=True,
            )
        else:
            st.markdown(badge("No blocking reasons", "good"), unsafe_allow_html=True)

        _show_live_ops_feedback()

        summary_left, summary_right = st.columns([0.95, 1.05])
        with summary_left:
            st.markdown('<div class="panel-title">Latest Action Snapshot</div>', unsafe_allow_html=True)
            if last_execution:
                st.markdown(
                    " ".join(
                        [
                            badge(str(last_execution["symbol"]), "info"),
                            badge(str(last_execution["side"]).upper(), "warn"),
                            badge(
                                str(last_execution["state"]).upper(),
                                "good" if str(last_execution["state"]) in {"filled", "canceled"} else "info",
                            ),
                        ]
                    ),
                    unsafe_allow_html=True,
                )
                st.caption(f"id={last_execution['id']} | updated={last_execution['updated_at']}")
                if last_execution.get("message"):
                    st.caption(str(last_execution["message"]))
            else:
                st.caption("No execution history yet.")
        with summary_right:
            st.markdown('<div class="panel-title">Open Order Focus</div>', unsafe_allow_html=True)
            if open_execution_orders:
                focus_options = {
                    f"id={order['id']} | {order['symbol']} | {order['side']} | {order['state']}": order
                    for order in open_execution_orders
                }
                current_focus = st.selectbox(
                    "Selected Open Order",
                    list(focus_options.keys()),
                    key="live_ops_selected_order",
                )
                focus_order = focus_options[current_focus]
                st.markdown(
                    " ".join(
                        [
                            badge(focus_order["symbol"], "info"),
                            badge(str(focus_order["side"]).upper(), "warn"),
                            badge(str(focus_order["state"]).upper(), "info"),
                        ]
                    ),
                    unsafe_allow_html=True,
                )
                st.caption(f"created={focus_order['created_at']}")
                st.caption(f"updated={focus_order['updated_at']}")
                if focus_order.get("exchange_order_id"):
                    st.caption(f"exchange_order_id={focus_order['exchange_order_id']}")
                if focus_order.get("message"):
                    st.caption(str(focus_order["message"]))
            else:
                st.caption("No open live order is currently selected.")

    render_refreshable_fragment(auto_refresh_run_every, _render_live_ops_dynamic_top)

    left, right = st.columns([1.1, 0.9])
    with left:
        st.markdown('<div class="panel-title">Manual Live Order</div>', unsafe_allow_html=True)
        with st.form("manual_live_order_form"):
            symbol = st.selectbox(
                "Symbol",
                symbols,
                index=max(0, symbols.index(default_symbol)) if default_symbol in symbols else 0,
            )
            side = st.selectbox("Side", ["buy", "sell"], index=0 if manual_defaults.get("side", "buy") == "buy" else 1)
            order_type = st.selectbox("Order Type", ["limit"], index=0)
            amount_thb = st.number_input(
                "Amount THB",
                min_value=0.0,
                value=float(manual_defaults.get("amount_thb", 100.0)),
                step=10.0,
            )
            amount_coin = st.number_input(
                "Amount Coin",
                min_value=0.0,
                value=float(manual_defaults.get("amount_coin", 0.0)),
                format="%.8f",
            )
            default_rate = float(latest_prices.get(symbol, manual_defaults.get("rate", 1.0)))
            rate = st.number_input("Rate", min_value=0.0, value=default_rate, format="%.8f")
            confirm = st.checkbox("I understand this can submit a real Bitkub order.")
            submitted = st.form_submit_button("Submit Manual Order", type="primary", use_container_width=True)

        selected_rule = dict(config["rules"][symbol])
        live_asset = symbol.split("_", 1)[1] if "_" in symbol else symbol
        live_asset_balance = float(available_balances.get(live_asset, 0.0))
        implied_coin_qty = (float(amount_thb) / float(rate)) if float(rate) > 0 else 0.0
        buy_hint_lines = [
            f"Rule budget: {float(selected_rule['budget_thb']):,.2f} THB",
            f"Live max order: {float(config['live_max_order_thb']):,.2f} THB",
            f"THB available: {thb_available:,.2f} THB",
            f"Approx qty at rate {float(rate):,.8f}: {implied_coin_qty:,.8f} {live_asset}",
        ]
        sell_hint_lines = [
            f"{live_asset} available: {live_asset_balance:,.8f}",
            f"Requested sell qty: {float(amount_coin):,.8f}",
            f"Last market price: {float(latest_prices.get(symbol, 0.0)):,.8f}",
            f"Configured sell_above: {float(selected_rule['sell_above']):,.8f}",
        ]
        validation_badges: list[str] = []
        if side == "buy" and float(amount_thb) > float(config["live_max_order_thb"]):
            validation_badges.append(badge("Amount exceeds live_max_order_thb", "bad"))
        if side == "buy" and float(amount_thb) > thb_available:
            validation_badges.append(badge("Amount exceeds THB available", "warn"))
        if side == "sell" and float(amount_coin) > live_asset_balance:
            validation_badges.append(badge("Amount exceeds asset available", "bad"))
        if float(rate) <= 0:
            validation_badges.append(badge("Rate must be greater than 0", "bad"))

        st.markdown('<div class="panel-title">Pre-flight Checks</div>', unsafe_allow_html=True)
        if validation_badges:
            st.markdown(" ".join(validation_badges), unsafe_allow_html=True)
        else:
            st.markdown(badge("Input looks internally consistent", "good"), unsafe_allow_html=True)
        for line in (buy_hint_lines if side == "buy" else sell_hint_lines):
            st.caption(line)
        st.caption(
            "The final decision still comes from execution guardrails and Bitkub responses at submit time."
        )

        if submitted:
            if not confirm:
                st.error("Confirmation is required before submitting a live order.")
            else:
                try:
                    execution_order_id, order_record = submit_manual_order_from_ui(
                        client=client,
                        config=config,
                        runtime=runtime,
                        private_capabilities=private_ctx["private_api_capabilities"],
                        account_snapshot=account_snapshot,
                        form_values={
                            "enabled": True,
                            "symbol": symbol,
                            "side": side,
                            "order_type": order_type,
                            "amount_thb": float(amount_thb),
                            "amount_coin": float(amount_coin),
                            "rate": float(rate),
                        },
                    )
                except LiveExecutionGuardrailError as e:
                    st.error(str(e))
                except Exception as e:
                    st.error(str(e))
                else:
                    _set_live_ops_feedback(
                        "Manual live order submitted",
                        [
                            f"id={execution_order_id} | symbol={order_record['symbol']} | side={order_record['side']}",
                            f"state={order_record['state']}",
                        ],
                    )
                    st.success(
                        f"Submitted execution order id={execution_order_id} symbol={order_record['symbol']} side={order_record['side']} state={order_record['state']}"
                    )
                    st.rerun()

    with right:
        def _render_live_controls() -> None:
            dynamic = _load_live_ops_dynamic()
            guardrails = dynamic["guardrails"]
            open_execution_orders = dynamic["open_execution_orders"]

            st.markdown('<div class="panel-title">Live Controls</div>', unsafe_allow_html=True)
            if st.button("Refresh Open Live Orders", use_container_width=True):
                refreshed = 0
                for order in open_execution_orders:
                    refreshed_record, events = refresh_live_order_from_exchange(
                        client=client,
                        order_record=order,
                        occurred_at=now_text(),
                    )
                    persist_execution_order_update(int(order["id"]), refreshed_record, events)
                    refreshed += 1
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="live_order_refresh_ui",
                    severity="info",
                    message="Live orders refreshed from Streamlit UI",
                    details={"count": refreshed},
                )
                _set_live_ops_feedback(
                    "Live order refresh completed",
                    [f"refreshed_open_orders={refreshed}"],
                )
                st.success(f"Refreshed {refreshed} open live order(s).")
                st.rerun()

            if open_execution_orders:
                option_map = {
                    f"id={order['id']} | {order['symbol']} | {order['side']} | {order['state']}": order
                    for order in open_execution_orders
                }
                current_focus = st.session_state.get("live_ops_selected_order")
                default_cancel_targets = [current_focus] if current_focus in option_map else []
                selected_labels = st.multiselect(
                    "Cancel Targets",
                    list(option_map.keys()),
                    default=default_cancel_targets,
                    key="live_ops_cancel_targets",
                    help="Choose one or more open execution orders to cancel in this round.",
                )
                confirm_cancel = st.checkbox("Confirm live cancel request", key="live_ops_confirm_cancel")
                if st.button("Cancel Selected Orders", use_container_width=True):
                    if not confirm_cancel:
                        st.error("Tick confirm before canceling a live order.")
                    elif not selected_labels:
                        st.error("Select at least one order to cancel.")
                    else:
                        success_lines: list[str] = []
                        error_lines: list[str] = []
                        for selected_label in selected_labels:
                            target_order = option_map[selected_label]
                            try:
                                canceled_record, events = cancel_live_order(
                                    client=client,
                                    order_record=target_order,
                                    occurred_at=now_text(),
                                )
                                persist_execution_order_update(int(target_order["id"]), canceled_record, events)
                                insert_runtime_event(
                                    created_at=now_text(),
                                    event_type="live_order_cancel_ui",
                                    severity="warning",
                                    message="Live order canceled from Streamlit UI",
                                    details={"execution_order_id": int(target_order["id"])} ,
                                )
                                success_lines.append(
                                    f"id={target_order['id']} | symbol={target_order['symbol']} | state={canceled_record['state']}"
                                )
                            except Exception as e:
                                error_lines.append(
                                    f"id={target_order['id']} | symbol={target_order['symbol']} | error={e}"
                                )

                        if error_lines and success_lines:
                            _set_live_ops_feedback(
                                "Live cancel completed with partial failures",
                                success_lines + error_lines,
                                tone="warning",
                            )
                            st.warning("Some cancel requests failed. See feedback panel above.")
                        elif error_lines:
                            _set_live_ops_feedback(
                                "Live cancel failed",
                                error_lines,
                                tone="error",
                            )
                            st.error("All selected cancel requests failed.")
                        else:
                            _set_live_ops_feedback(
                                "Live cancel completed",
                                success_lines,
                            )
                            st.success(f"Canceled {len(success_lines)} live order(s).")
                        st.rerun()
            else:
                st.caption("No open live orders are available for cancel.")

            with st.expander("Guardrails JSON", expanded=False):
                st.json(guardrails, expanded=False)

        render_refreshable_fragment(auto_refresh_run_every, _render_live_controls)

    def _render_live_ops_history() -> None:
        dynamic = _load_live_ops_dynamic()
        recent_execution_orders = dynamic["recent_execution_orders"]
        recent_execution_events = dynamic["recent_execution_events"]

        history_left, history_right = st.columns([1.0, 1.0])
        with history_left:
            st.markdown('<div class="panel-title">Recent Execution Orders</div>', unsafe_allow_html=True)
            st.dataframe(recent_execution_orders, use_container_width=True, hide_index=True)
        with history_right:
            st.markdown('<div class="panel-title">Recent Execution Events</div>', unsafe_allow_html=True)
            st.dataframe(recent_execution_events, use_container_width=True, hide_index=True)

    render_refreshable_fragment(auto_refresh_run_every, _render_live_ops_history)


def render_strategy_page(*, config: dict[str, Any]) -> None:
    st.markdown('<div class="panel-title">Strategy Lab</div>', unsafe_allow_html=True)
    st.caption(
        "Use actual paper-trade logs, stored market snapshots, and stored candles to evaluate whether the current rule set has edge. "
        "Watchlist symbols act as the research universe, while config rules remain the live trading shortlist."
    )

    analytics = fetch_trade_analytics()
    totals = analytics["totals"]
    coverage_days = int(config.get("market_snapshot_retention_days", 30))
    coverage_rows = fetch_market_snapshot_coverage(days=coverage_days)

    card1, card2, card3, card4 = st.columns(4)
    with card1:
        render_metric_card("Actual Trades", str(totals["trades"]), f"Win rate {totals['win_rate_percent']:.2f}%")
    with card2:
        render_metric_card("Actual PnL", f"{totals['total_pnl_thb']:,.2f} THB", f"Avg/trade {totals['avg_pnl_thb']:,.2f}")
    with card3:
        render_metric_card("Profit Factor", f"{totals['profit_factor']:.2f}", f"Avg win {totals['avg_win_thb']:,.2f}")
    with card4:
        render_metric_card("Hold Time", f"{totals['avg_hold_minutes']:.1f} min", f"Losses {totals['losses']}")

    top_left, top_right = st.columns([1.15, 0.85])
    with top_left:
        st.markdown('<div class="panel-title">Actual Trade Analytics by Symbol</div>', unsafe_allow_html=True)
        if analytics["by_symbol"]:
            st.dataframe(analytics["by_symbol"], use_container_width=True, hide_index=True)
        else:
            st.caption("No paper trade history exists yet. Run the paper bot longer or import prior trade logs first.")
    with top_right:
        st.markdown('<div class="panel-title">Exit Reason Breakdown</div>', unsafe_allow_html=True)
        if analytics["by_exit_reason"]:
            st.dataframe(analytics["by_exit_reason"], use_container_width=True, hide_index=True)
        else:
            st.caption("No exit reasons are available because no paper trades have been stored yet.")

    with st.expander("Recent Actual Paper Trades", expanded=False):
        if analytics["recent_trades"]:
            st.dataframe(analytics["recent_trades"], use_container_width=True, hide_index=True)
        else:
            st.caption("No recent paper trades available.")

    st.markdown('<div class="panel-title">Candle Sync & Coin Ranking</div>', unsafe_allow_html=True)
    st.caption(
        "Sync TradingView history into SQLite first, then rank coins by recent momentum, range position, stability, and average volume."
    )

    market_universe = _fetch_market_symbol_universe()
    configured_symbols = sorted(config["rules"].keys())
    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", configured_symbols)
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    market_symbols = list(market_universe.get("symbols", []))
    symbols = watchlist_symbols or market_symbols or configured_symbols
    if market_universe.get("error"):
        st.warning(f"Bitkub market symbols unavailable right now: {market_universe['error']}")
    if market_symbols:
        st.caption(
            f"Market universe loaded: {len(market_symbols)} symbol(s) | watchlist: {len(watchlist_symbols)} | live rules: {len(configured_symbols)}"
        )

    rank_resolution_options = ["1", "5", "15", "60", "240", "1D"]
    default_rank_resolution = str(st.session_state.get("strategy_rank_resolution", "240"))
    if default_rank_resolution not in rank_resolution_options:
        default_rank_resolution = "240"
    default_rank_days = int(st.session_state.get("strategy_rank_days", 14))

    with st.form("strategy_candle_sync_form"):
        sync_col1, sync_col2, sync_col3 = st.columns([0.4, 0.3, 0.3])
        with sync_col1:
            selected_sync_symbols = st.multiselect(
                "Symbols to Sync",
                symbols,
                default=watchlist_symbols or configured_symbols or symbols[: min(len(symbols), 10)],
                help="This list defaults to the watchlist. Stored candles are used by the ranking table below.",
            )
        with sync_col2:
            ranking_resolution = st.selectbox(
                "Candle Resolution",
                rank_resolution_options,
                index=rank_resolution_options.index(default_rank_resolution),
            )
        with sync_col3:
            ranking_days = st.number_input(
                "Lookback Days",
                min_value=1,
                max_value=90,
                value=default_rank_days,
                step=1,
            )
        run_candle_sync = st.form_submit_button("Sync Candles", type="primary", use_container_width=True)

    if run_candle_sync:
        sync_result = sync_candles_for_symbols(
            symbols=selected_sync_symbols or symbols,
            resolution=str(ranking_resolution),
            days=int(ranking_days),
        )
        st.session_state["strategy_candle_sync_result"] = sync_result
        st.session_state["strategy_rank_resolution"] = str(ranking_resolution)
        st.session_state["strategy_rank_days"] = int(ranking_days)

    sync_feedback = st.session_state.get("strategy_candle_sync_result")
    if sync_feedback:
        if sync_feedback.get("synced"):
            st.success(
                f"Synced candles for {len(sync_feedback['synced'])} symbol(s) | resolution={sync_feedback['resolution']} | days={sync_feedback['days']}"
            )
            with st.expander("Sync Result Details", expanded=False):
                st.dataframe(sync_feedback["synced"], use_container_width=True, hide_index=True)
        if sync_feedback.get("errors"):
            summarized_sync_errors = _summarize_text_lines(list(sync_feedback["errors"]))
            no_data_count = sum(row["count"] for row in summarized_sync_errors if "history status=no_data" in str(row["message"]))
            st.warning(
                f"Sync warnings: {len(sync_feedback['errors'])} total | no_data {no_data_count}"
            )
            with st.expander("Sync Warning Summary", expanded=False):
                st.dataframe(summarized_sync_errors, use_container_width=True, hide_index=True)

    ranking_resolution = str(st.session_state.get("strategy_rank_resolution", default_rank_resolution))
    ranking_days = int(st.session_state.get("strategy_rank_days", default_rank_days))
    ranking = build_coin_ranking(
        symbols=symbols,
        resolution=ranking_resolution,
        lookback_days=ranking_days,
    )

    rank_card1, rank_card2, rank_card3 = st.columns(3)
    with rank_card1:
        render_metric_card("Ranking Resolution", ranking_resolution, f"Lookback {ranking_days} day(s)")
    with rank_card2:
        render_metric_card("Ranked Symbols", str(len(ranking["rows"])), f"Coverage rows {len(ranking['coverage'])}")
    with rank_card3:
        top_score = ranking["rows"][0]["score"] if ranking["rows"] else 0.0
        render_metric_card("Top Score", f"{top_score:.2f}", "Higher = stronger trend shortlist")

    auto_entry_min_score = float(config.get("live_auto_entry_min_score", 50.0))
    auto_entry_allowed_biases = {
        str(value).strip().lower()
        for value in config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"])
        if str(value).strip()
    } or {"bullish", "mixed"}
    shortlist_rows = []
    for row in ranking["rows"]:
        row_bias = str(row.get("trend_bias") or "").lower()
        passes = float(row.get("score", 0.0) or 0.0) >= auto_entry_min_score and row_bias in auto_entry_allowed_biases
        in_live_rules = row["symbol"] in config["rules"]
        shortlist_rows.append(
            {
                "symbol": row["symbol"],
                "score": row["score"],
                "trend_bias": row["trend_bias"],
                "in_live_rules": in_live_rules,
                "recommendation": "LIVE_READY" if passes and in_live_rules else "PROMOTE" if passes else "REVIEW",
                "last_close": row["last_close"],
                "position_in_range": row["position_in_range"],
                "momentum_pct": row["momentum_pct"],
            }
        )

    recommended_promotions = [
        row["symbol"]
        for row in shortlist_rows
        if row["recommendation"] == "PROMOTE"
    ]

    rank_left, rank_right = st.columns([1.15, 0.85])
    with rank_left:
        st.markdown('<div class="panel-title">Coin Ranking</div>', unsafe_allow_html=True)
        if ranking["rows"]:
            st.dataframe(ranking["rows"], use_container_width=True, hide_index=True)

            promotable_symbols = [
                row["symbol"]
                for row in ranking["rows"]
                if row["symbol"] not in config["rules"]
            ]
            with st.form("strategy_promote_ranked_symbols_form"):
                selected_promotions = st.multiselect(
                    "Promote ranked symbols into live rules",
                    promotable_symbols,
                    default=(recommended_promotions or promotable_symbols)[: min(len(recommended_promotions or promotable_symbols), 5)],
                    help="Adds a conservative starter rule for each selected ranked symbol and keeps them in the watchlist.",
                )
                submitted_promotions = st.form_submit_button(
                    "Add Selected Ranked Symbols",
                    use_container_width=True,
                )
            if submitted_promotions:
                if not selected_promotions:
                    st.warning("Select at least one ranked symbol to promote.")
                else:
                    updated = dict(config)
                    updated_rules = dict(config["rules"])
                    for promoted_symbol in selected_promotions:
                        if promoted_symbol not in updated_rules:
                            updated_rules[promoted_symbol] = _build_rule_seed(config, promoted_symbol)
                    updated["rules"] = updated_rules
                    updated["watchlist_symbols"] = sorted(
                        set(config.get("watchlist_symbols", configured_symbols)) | set(selected_promotions)
                    )
                    if _save_config_with_feedback(
                        config,
                        updated,
                        f"Promoted {len(selected_promotions)} ranked symbol(s) into live rules",
                    ):
                        st.rerun()
        else:
            st.caption("No ranked symbols yet. Sync candles first or widen the lookback window.")
    with rank_right:
        st.markdown('<div class="panel-title">Stored Candle Coverage</div>', unsafe_allow_html=True)
        if ranking["coverage"]:
            st.dataframe(ranking["coverage"], use_container_width=True, hide_index=True)
        else:
            st.caption("No stored candles available yet.")
        if ranking.get("errors"):
            summarized_ranking_errors = _summarize_text_lines(list(ranking["errors"]))
            no_data_count = sum(row["count"] for row in summarized_ranking_errors if "not enough stored candles" in str(row["message"]))
            st.markdown('<div class="panel-title">Ranking Notes</div>', unsafe_allow_html=True)
            st.caption(f"{len(ranking['errors'])} note(s) | insufficient-candle rows {no_data_count}")
            with st.expander("Ranking Note Summary", expanded=False):
                st.dataframe(summarized_ranking_errors, use_container_width=True, hide_index=True)

    st.markdown('<div class="panel-title">Auto Entry Shortlist</div>', unsafe_allow_html=True)
    st.caption(
        f"Shortlist uses current config filters: min_score >= {auto_entry_min_score:.1f}, biases = {', '.join(sorted(auto_entry_allowed_biases))}."
    )
    shortlist_left, shortlist_right = st.columns([1.0, 1.0])
    with shortlist_left:
        live_ready_rows = [row for row in shortlist_rows if row["recommendation"] == "LIVE_READY"]
        if live_ready_rows:
            st.dataframe(live_ready_rows[:12], use_container_width=True, hide_index=True)
        else:
            st.caption("No current live rules pass the auto-entry shortlist filters.")
    with shortlist_right:
        promote_rows = [row for row in shortlist_rows if row["recommendation"] == "PROMOTE"]
        if promote_rows:
            st.dataframe(promote_rows[:12], use_container_width=True, hide_index=True)
        else:
            st.caption("No extra watchlist symbols are currently strong enough to promote.")

    st.markdown('<div class="panel-title">Replay Lab</div>', unsafe_allow_html=True)
    st.caption(
        "Replay can now run on stored candles or stored market snapshots. "
        "Use candles for ranked symbols first; snapshots remain available for the older console-style feed."
    )

    default_symbol = st.session_state.get("strategy_replay_symbol", symbols[0])
    if default_symbol not in symbols:
        default_symbol = symbols[0]
    replay_source_options = ["candles", "snapshots"]
    default_replay_source = str(st.session_state.get("strategy_replay_source", "candles"))
    if default_replay_source not in replay_source_options:
        default_replay_source = "candles"
    default_replay_resolution = str(st.session_state.get("strategy_replay_resolution", ranking_resolution))
    if default_replay_resolution not in rank_resolution_options:
        default_replay_resolution = ranking_resolution

    with st.form("strategy_replay_form"):
        replay_meta_left, replay_meta_right = st.columns(2)
        with replay_meta_left:
            replay_symbol = st.selectbox(
                "Replay Symbol",
                symbols,
                index=symbols.index(default_symbol),
            )
            replay_source = st.selectbox(
                "Replay Source",
                replay_source_options,
                index=replay_source_options.index(default_replay_source),
                help="Candles use TradingView history stored in SQLite. Snapshots use the older market_snapshots feed.",
            )
        with replay_meta_right:
            replay_resolution = st.selectbox(
                "Replay Candle Resolution",
                rank_resolution_options,
                index=rank_resolution_options.index(default_replay_resolution),
                help="Used only when Replay Source = candles.",
            )
            lookback_days = st.number_input(
                "Replay Lookback Days",
                min_value=1,
                max_value=90,
                value=int(st.session_state.get("strategy_replay_days", min(14, max(14, coverage_days)))),
                step=1,
            )
        active_rule = _build_rule_seed(config, replay_symbol)
        form_left, form_right = st.columns(2)
        with form_left:
            buy_below = st.number_input("Buy Below", min_value=0.0, value=float(active_rule["buy_below"]), format="%.8f")
            sell_above = st.number_input("Sell Above", min_value=0.0, value=float(active_rule["sell_above"]), format="%.8f")
            budget_thb = st.number_input("Budget THB", min_value=1.0, value=float(active_rule["budget_thb"]), step=10.0)
            max_trades_per_day = st.number_input(
                "Max Trades / Day",
                min_value=1,
                value=int(active_rule["max_trades_per_day"]),
                step=1,
            )
        with form_right:
            stop_loss_percent = st.number_input(
                "Stop Loss %",
                min_value=0.01,
                value=float(active_rule["stop_loss_percent"]),
                format="%.2f",
            )
            take_profit_percent = st.number_input(
                "Take Profit %",
                min_value=0.01,
                value=float(active_rule["take_profit_percent"]),
                format="%.2f",
            )
            cooldown_seconds = st.number_input(
                "Cooldown Seconds",
                min_value=0,
                value=int(config["cooldown_seconds"]),
                step=1,
            )
            fee_rate = st.number_input(
                "Fee Rate",
                min_value=0.0,
                max_value=0.9999,
                value=float(config["fee_rate"]),
                format="%.6f",
            )
        run_replay = st.form_submit_button("Run Replay", type="primary", use_container_width=True)

    if run_replay or "strategy_replay_result" not in st.session_state:
        replay_rule = {
            "buy_below": float(buy_below),
            "sell_above": float(sell_above),
            "budget_thb": float(budget_thb),
            "stop_loss_percent": float(stop_loss_percent),
            "take_profit_percent": float(take_profit_percent),
            "max_trades_per_day": int(max_trades_per_day),
        }
        if replay_source == "candles":
            replay_result = run_market_candle_replay(
                symbol=replay_symbol,
                resolution=str(replay_resolution),
                rule=replay_rule,
                fee_rate=float(fee_rate),
                cooldown_seconds=int(cooldown_seconds),
                days=int(lookback_days),
            )
        else:
            replay_result = run_market_snapshot_replay(
                symbol=replay_symbol,
                rule=replay_rule,
                fee_rate=float(fee_rate),
                cooldown_seconds=int(cooldown_seconds),
                days=int(lookback_days),
            )
        st.session_state["strategy_replay_result"] = replay_result
        st.session_state["strategy_replay_symbol"] = replay_symbol
        st.session_state["strategy_replay_days"] = int(lookback_days)
        st.session_state["strategy_replay_source"] = replay_source
        st.session_state["strategy_replay_resolution"] = str(replay_resolution)

    replay = st.session_state.get("strategy_replay_result")
    if replay:
        metrics = replay["metrics"]
        replay_card1, replay_card2, replay_card3, replay_card4 = st.columns(4)
        with replay_card1:
            render_metric_card("Replay Trades", str(metrics["trades"]), f"{replay.get('source', 'replay')} rows {replay.get('candles', replay.get('snapshots', replay.get('bars', 0)))}")
        with replay_card2:
            render_metric_card("Replay PnL", f"{metrics['total_pnl_thb']:,.2f} THB", f"Win rate {metrics['win_rate_percent']:.2f}%")
        with replay_card3:
            render_metric_card("Replay Avg/Trade", f"{metrics['avg_pnl_thb']:,.2f}", f"Profit factor {metrics['profit_factor']:.2f}")
        with replay_card4:
            render_metric_card("Replay Hold", f"{metrics['avg_hold_minutes']:.1f} min", f"W {metrics['wins']} / L {metrics['losses']}")

        replay_left, replay_right = st.columns([1.05, 0.95])
        with replay_left:
            st.markdown('<div class="panel-title">Replay Trades</div>', unsafe_allow_html=True)
            if replay["trades"]:
                st.dataframe(replay["trades"], use_container_width=True, hide_index=True)
            else:
                st.caption("No replay exits were generated for this symbol and parameter set.")
        with replay_right:
            st.markdown('<div class="panel-title">Replay Coverage</div>', unsafe_allow_html=True)
            coverage = replay.get("coverage")
            if coverage:
                st.caption(f"first_seen={coverage['first_seen']}")
                st.caption(f"last_seen={coverage['last_seen']}")
                st.caption(f"min_price={float(coverage['min_price']):,.8f}")
                st.caption(f"max_price={float(coverage['max_price']):,.8f}")
            else:
                st.caption("No snapshot coverage available.")

            if replay.get("open_position"):
                st.markdown('<div class="panel-title">Open Position at Replay End</div>', unsafe_allow_html=True)
                st.json(replay["open_position"], expanded=False)

            if replay.get("notes"):
                st.markdown('<div class="panel-title">Replay Notes</div>', unsafe_allow_html=True)
                for note in replay["notes"]:
                    st.caption(note)

    st.markdown('<div class="panel-title">Snapshot Coverage by Symbol</div>', unsafe_allow_html=True)
    if coverage_rows:
        st.dataframe(coverage_rows, use_container_width=True, hide_index=True)
    else:
        st.caption("No market snapshot coverage was found in SQLite yet.")


def render_reports_page(*, today: str, config: dict[str, Any]) -> None:
    symbols = ["ALL"] + sorted(config["rules"].keys())
    selected_symbol = st.selectbox("Report Filter", symbols, index=0)
    report = fetch_reporting_summary(today=today, symbol=None if selected_symbol == "ALL" else selected_symbol)
    symbol_summary = report["symbol_summary"]
    recent_execution_orders = report["recent_execution_orders"]
    recent_auto_exit_events = report["recent_auto_exit_events"]
    recent_errors = report["recent_errors"]
    recent_trades = report["recent_trades"]

    total_signals = sum(int(row.get("signals", 0)) for row in symbol_summary)
    total_trades = sum(int(row.get("trades", 0)) for row in symbol_summary)
    total_pnl = sum(float(row.get("pnl_thb", 0.0)) for row in symbol_summary)
    total_wins = sum(int(row.get("wins", 0)) for row in symbol_summary)
    total_losses = sum(int(row.get("losses", 0)) for row in symbol_summary)

    card1, card2, card3, card4 = st.columns(4)
    with card1:
        render_metric_card("Report Filter", selected_symbol, f"Symbols {len(symbol_summary)}")
    with card2:
        render_metric_card("Signals Today", str(total_signals), f"Trades {total_trades}")
    with card3:
        render_metric_card("PnL Today", f"{total_pnl:,.2f} THB", f"W {total_wins} / L {total_losses}")
    with card4:
        render_metric_card(
            "Execution Activity",
            str(len(recent_execution_orders)),
            f"Auto exits {len(recent_auto_exit_events)} | Errors {len(recent_errors)}",
        )

    left, right = st.columns([1.15, 0.85])
    with left:
        st.markdown('<div class="panel-title">Symbol Summary</div>', unsafe_allow_html=True)
        if symbol_summary:
            st.dataframe(symbol_summary, use_container_width=True, hide_index=True)
        else:
            st.caption("No symbol summary rows for the current filter and date.")
    with right:
        st.markdown('<div class="panel-title">Recent Paper Trades</div>', unsafe_allow_html=True)
        if recent_trades:
            st.dataframe(recent_trades, use_container_width=True, hide_index=True)
        else:
            st.caption("No paper trades stored for this filter yet.")

    bottom_left, bottom_right = st.columns([1.0, 1.0])
    with bottom_left:
        st.markdown('<div class="panel-title">Recent Execution Orders</div>', unsafe_allow_html=True)
        if recent_execution_orders:
            st.dataframe(recent_execution_orders, use_container_width=True, hide_index=True)
        else:
            st.caption("No execution orders stored for this filter yet.")
        st.markdown('<div class="panel-title">Recent Auto Exit Events</div>', unsafe_allow_html=True)
        if recent_auto_exit_events:
            st.dataframe(recent_auto_exit_events, use_container_width=True, hide_index=True)
        else:
            st.caption("No auto-exit events stored for this filter yet.")
    with bottom_right:
        st.markdown('<div class="panel-title">Recent Runtime Errors</div>', unsafe_allow_html=True)
        if recent_errors:
            st.dataframe(recent_errors, use_container_width=True, hide_index=True)
        else:
            st.caption("No recent runtime errors recorded.")


def render_logs_page(*, private_ctx: dict[str, Any]) -> None:
    st.markdown('<div class="panel-title">Logs & Errors</div>', unsafe_allow_html=True)
    st.caption(
        "Use this page to separate current issues from historical runtime events. Categories and hints are there to speed up troubleshooting."
    )

    current_issue_rows = _current_private_api_issues(private_ctx)
    historical_rows = [_classify_runtime_event(row) for row in fetch_runtime_event_log(limit=200)]
    telegram_rows = fetch_recent_telegram_outbox(limit=50)
    telegram_command_rows = fetch_recent_telegram_command_log(limit=50)
    telegram_settings = telegram_settings_snapshot(reload_config()[0] or {})

    category_counts: dict[str, int] = defaultdict(int)
    for row in current_issue_rows + historical_rows:
        category_counts[str(row.get("category") or "General")] += 1

    card1, card2, card3, card4 = st.columns(4)
    with card1:
        render_metric_card("Current Issues", str(len(current_issue_rows)), "Current snapshot only")
    with card2:
        render_metric_card("Historical Events", str(len(historical_rows)), "Last 200 runtime events")
    with card3:
        render_metric_card("Error Events", str(sum(1 for row in historical_rows if row.get("severity") == "error")), "Persisted runtime severity=error")
    with card4:
        top_category = max(category_counts, key=category_counts.get) if category_counts else "None"
        render_metric_card("Top Category", top_category, f"Telegram queued {len(telegram_rows)}")

    current_left, current_right = st.columns([1.0, 1.0])
    with current_left:
        st.markdown('<div class="panel-title">Current Issues</div>', unsafe_allow_html=True)
        if current_issue_rows:
            st.dataframe(current_issue_rows, use_container_width=True, hide_index=True)
        else:
            st.caption("No current snapshot issues detected.")
    with current_right:
        st.markdown('<div class="panel-title">Category Summary</div>', unsafe_allow_html=True)
        if category_counts:
            st.dataframe(
                [
                    {"category": category, "count": count}
                    for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No runtime categories recorded yet.")

    st.markdown('<div class="panel-title">Telegram Delivery Readiness</div>', unsafe_allow_html=True)
    readiness_cols = st.columns(4)
    with readiness_cols[0]:
        render_metric_card("Telegram Enabled", "ON" if telegram_settings["enabled"] else "OFF", "config.json toggle")
    with readiness_cols[1]:
        render_metric_card("Bot Token", "PRESENT" if telegram_settings["bot_token_present"] else "MISSING", "from .env")
    with readiness_cols[2]:
        render_metric_card("Chat IDs", str(len(telegram_settings["chat_ids"])), "from .env")
    with readiness_cols[3]:
        render_metric_card("Delivery Ready", "YES" if telegram_settings["ready"] else "NO", "control YES" if telegram_settings["control_ready"] else "control NO")
    st.caption("Telegram sender uses TELEGRAM_BOT_TOKEN plus TELEGRAM_CHAT_IDS or TELEGRAM_CHAT_ID. Telegram commands can be restricted with TELEGRAM_ALLOWED_CHAT_IDS.")

    latest_auto_entry_review = next(
        (
            row
            for row in historical_rows
            if str(row.get("event_type") or "") == "auto_live_entry_review"
        ),
        None,
    )

    st.markdown('<div class="panel-title">Latest Auto Entry Review</div>', unsafe_allow_html=True)
    if latest_auto_entry_review:
        review_details = dict(latest_auto_entry_review.get("details") or {})
        review_candidates = list(review_details.get("candidates") or [])
        review_rejected = list(review_details.get("rejected") or [])
        review_context = dict(review_details.get("ranking_context") or {})
        review_cards = st.columns(4)
        with review_cards[0]:
            render_metric_card("Review Time", str(latest_auto_entry_review.get("created_at") or "n/a"), str(review_details.get("ranking_context", {}).get("resolution") or "n/a"))
        with review_cards[1]:
            render_metric_card("Candidates", str(len(review_candidates)), f"Rejected {len(review_rejected)}")
        with review_cards[2]:
            render_metric_card("Min Score", f"{float(review_context.get('min_score', 0.0)):.1f}", f"Require ranking {'ON' if review_context.get('require_ranking') else 'OFF'}")
        with review_cards[3]:
            render_metric_card("Allowed Biases", ", ".join(review_context.get("allowed_biases", [])) or "n/a", f"Lookback {review_context.get('lookback_days', 'n/a')}")

        review_left, review_right = st.columns([1.0, 1.0])
        with review_left:
            if review_candidates:
                st.dataframe(review_candidates, use_container_width=True, hide_index=True)
            else:
                st.caption("No auto-entry candidates passed the current filters in the latest review.")
        with review_right:
            if review_rejected:
                st.dataframe(review_rejected, use_container_width=True, hide_index=True)
            else:
                st.caption("No rejected candidates were recorded in the latest review.")
    else:
        st.caption("No auto-entry review has been recorded yet.")

    log_categories = ["ALL"] + sorted({str(row.get("category") or "General") for row in historical_rows})
    selected_category = st.selectbox("Log Category Filter", log_categories, index=0, key="logs_category_filter")
    filtered_rows = [
        row
        for row in historical_rows
        if selected_category == "ALL" or str(row.get("category")) == selected_category
    ]

    st.markdown('<div class="panel-title">Historical Runtime Events</div>', unsafe_allow_html=True)
    if filtered_rows:
        st.dataframe(
            [
                {
                    "created_at": row.get("created_at"),
                    "severity": row.get("severity"),
                    "category": row.get("category"),
                    "topic": row.get("topic"),
                    "event_type": row.get("event_type"),
                    "message": row.get("message"),
                    "hint": row.get("hint"),
                }
                for row in filtered_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No runtime events match the selected category.")

    with st.expander("Event Details", expanded=False):
        detailed_rows = [
            {
                "created_at": row.get("created_at"),
                "event_type": row.get("event_type"),
                "severity": row.get("severity"),
                "category": row.get("category"),
                "topic": row.get("topic"),
                "message": row.get("message"),
                "hint": row.get("hint"),
                "details": row.get("details", {}),
            }
            for row in filtered_rows[:50]
        ]
        if detailed_rows:
            st.json(detailed_rows, expanded=False)
        else:
            st.caption("No detailed events to show.")

    st.markdown('<div class="panel-title">Telegram Outbox</div>', unsafe_allow_html=True)
    if telegram_rows:
        st.dataframe(
            [
                {
                    "created_at": row.get("created_at"),
                    "event_type": row.get("event_type"),
                    "status": row.get("status"),
                    "title": row.get("title"),
                }
                for row in telegram_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("Telegram Outbox Details", expanded=False):
            st.json(telegram_rows[:25], expanded=False)
    else:
        st.caption("No Telegram notifications have been queued yet.")


    st.markdown('<div class="panel-title">Telegram Command Log</div>', unsafe_allow_html=True)
    if telegram_command_rows:
        st.dataframe(
            [
                {
                    "created_at": row.get("created_at"),
                    "update_id": row.get("update_id"),
                    "chat_id": row.get("chat_id"),
                    "command": row.get("command_text"),
                    "status": row.get("status"),
                }
                for row in telegram_command_rows
            ],
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("Telegram Command Details", expanded=False):
            st.json(telegram_command_rows[:25], expanded=False)
    else:
        st.caption("No Telegram commands have been processed yet.")


def render_diagnostics_page(
    *,
    today: str,
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
) -> None:
    db_summary = fetch_db_maintenance_summary()
    dashboard_summary = fetch_dashboard_summary(today=today)
    execution_console_summary = fetch_execution_console_summary()
    live_reconciliation = summarize_live_reconciliation(
        execution_orders=fetch_open_execution_orders(),
        live_holdings_rows=build_live_holdings_snapshot(
            account_snapshot=private_ctx["account_snapshot"],
            latest_prices=latest_prices,
            latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
        ),
        account_snapshot=private_ctx["account_snapshot"],
        private_client=private_ctx["client"],
    )
    latest_account_snapshot = dashboard_summary.get("latest_account_snapshot")
    latest_reconciliation = dashboard_summary.get("latest_reconciliation")
    latest_execution_order = dashboard_summary.get("latest_execution_order")

    storage_card1, storage_card2, storage_card3, storage_card4 = st.columns(4)
    table_counts = dict(db_summary.get("table_counts", {}))
    latest_cleanup = dict(db_summary.get("latest_cleanup", {}))
    latest_cleanup_details = dict(latest_cleanup.get("details", {}))
    retention_days = dict(latest_cleanup_details.get("retention_days", {}))
    deleted_rows = dict(latest_cleanup_details.get("deleted_rows", {}))

    with storage_card1:
        render_metric_card(
            "DB Exists",
            "YES" if db_summary.get("db_exists") else "NO",
            f"Size {float(db_summary.get('db_size_bytes', 0)) / 1024:,.2f} KB",
        )
    with storage_card2:
        render_metric_card(
            "Runtime Events",
            str(table_counts.get("runtime_events", 0)),
            f"Signals {table_counts.get('signal_logs', 0)}",
        )
    with storage_card3:
        render_metric_card(
            "Snapshots",
            str(table_counts.get("market_snapshots", 0)),
            f"Accounts {table_counts.get('account_snapshots', 0)}",
        )
    with storage_card4:
        render_metric_card(
            "Execution Rows",
            str(table_counts.get("execution_orders", 0)),
            f"Events {table_counts.get('execution_order_events', 0)}",
        )

    col1, col2 = st.columns([0.95, 1.05])
    with col1:
        st.markdown('<div class="panel-title">SQLite Health</div>', unsafe_allow_html=True)
        st.caption(f"Latest cleanup: {latest_cleanup.get('created_at', 'n/a')}")
        st.caption(str(latest_cleanup.get("message", "No cleanup history")))
        retention_rows = [
            {
                "table": table_name,
                "days": days,
                "deleted_last_run": deleted_rows.get(table_name, 0),
            }
            for table_name, days in retention_days.items()
        ]
        if retention_rows:
            st.dataframe(retention_rows, use_container_width=True, hide_index=True)

        st.markdown('<div class="panel-title">Latest Records</div>', unsafe_allow_html=True)
        latest_rows = []
        if latest_account_snapshot:
            latest_rows.append(
                {
                    "type": "account_snapshot",
                    "time": latest_account_snapshot.get("created_at"),
                    "status": latest_account_snapshot.get("private_api_status"),
                }
            )
        if latest_reconciliation:
            latest_rows.append(
                {
                    "type": "reconciliation",
                    "time": latest_reconciliation.get("created_at"),
                    "status": latest_reconciliation.get("status"),
                }
            )
        if latest_execution_order:
            latest_rows.append(
                {
                    "type": "execution_order",
                    "time": latest_execution_order.get("updated_at"),
                    "status": latest_execution_order.get("state"),
                }
            )
        st.dataframe(latest_rows, use_container_width=True, hide_index=True)
    with col2:
        st.markdown('<div class="panel-title">Live Reconciliation</div>', unsafe_allow_html=True)
        render_reconciliation_block(live_reconciliation)
        st.markdown('<div class="panel-title">Execution Console Summary</div>', unsafe_allow_html=True)
        summary_rows = [
            {
                "group": "open_orders",
                "count": len(execution_console_summary["open_orders"]),
            },
            {
                "group": "recent_orders",
                "count": len(execution_console_summary["recent_orders"]),
            },
            {
                "group": "recent_events",
                "count": len(execution_console_summary["recent_events"]),
            },
        ]
        st.dataframe(summary_rows, use_container_width=True, hide_index=True)
        with st.expander("Execution Details", expanded=False):
            st.json(execution_console_summary, expanded=False)


def render_reconciliation_block(live_reconciliation: dict[str, Any]) -> None:
    groups = (
        ("warnings", "warn"),
        ("partially_filled_orders", "warn"),
        ("reserved_without_open_order", "bad"),
        ("open_order_without_reserved", "warn"),
        ("triggered_exit_candidates", "info"),
        ("unmanaged_live_holdings", "bad"),
    )
    printed = False
    for key, tone in groups:
        rows = live_reconciliation.get(key, [])
        if not rows:
            continue
        printed = True
        st.markdown(badge(key.replace("_", " "), tone), unsafe_allow_html=True)
        for row in rows:
            st.write(f"- {row}")
    if not printed:
        st.caption("No live reconciliation issues detected.")


def render_config_page(*, config: dict[str, Any]) -> None:
    st.markdown('<div class="panel-title">Config Editor</div>', unsafe_allow_html=True)
    st.caption(f"Source of truth: `{CONFIG_PATH}`")
    _show_config_save_feedback()

    market_universe = _fetch_market_symbol_universe()
    configured_symbols = sorted(config["rules"].keys())
    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", configured_symbols)
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    market_symbols = list(market_universe.get("symbols", []))
    available_new_symbols = [symbol for symbol in market_symbols if symbol not in config["rules"]]

    st.markdown(
        """
        <div class="note-strip">
          <strong>Apply Model</strong><br>
          Changes saved here write to <code>config.json</code> only.
          The console engine remains the runner, so it still needs its own reload/apply step.
        </div>
        """,
        unsafe_allow_html=True,
    )

    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    with summary_col1:
        render_metric_card("Mode", str(config["mode"]).upper(), f"Base URL {config['base_url']}")
    with summary_col2:
        render_metric_card(
            "Watchlist",
            str(len(watchlist_symbols)),
            f"Live rules {len(config['rules'])} | Market {len(market_symbols) if market_symbols else 'n/a'}",
        )
    with summary_col3:
        render_metric_card(
            "Live Controls",
            "ON" if bool(config["live_execution_enabled"]) else "OFF",
            (
                "Auto entry ON" if bool(config.get("live_auto_entry_enabled", False)) else "Auto entry OFF"
            ) + " | " + (
                "Auto exit ON" if bool(config.get("live_auto_exit_enabled", False)) else "Auto exit OFF"
            ),
        )
    with summary_col4:
        render_metric_card(
            "Manual Preset",
            str(config.get("live_manual_order", {}).get("symbol", "n/a")),
            str(config.get("live_manual_order", {}).get("side", "n/a")).upper(),
        )

    with st.expander("Live Rule Summary", expanded=False):
        st.dataframe(
            [
                {
                    "symbol": symbol,
                    "buy_below": float(rule["buy_below"]),
                    "sell_above": float(rule["sell_above"]),
                    "budget_thb": float(rule["budget_thb"]),
                    "stop_loss_percent": float(rule["stop_loss_percent"]),
                    "take_profit_percent": float(rule["take_profit_percent"]),
                    "max_trades_per_day": int(rule["max_trades_per_day"]),
                }
                for symbol, rule in sorted(config["rules"].items())
            ],
            use_container_width=True,
            hide_index=True,
        )

    left, right = st.columns([1, 1])

    with left:
        st.markdown("#### System Settings")
        st.caption("These fields shape the console engine behavior after it reloads config.")
        with st.form("config_system_form"):
            modes = ["paper", "read-only", "live-disabled", "live"]
            mode = st.selectbox("Mode", modes, index=modes.index(str(config["mode"])))
            base_url = st.text_input("Base URL", value=str(config["base_url"]))
            fee_rate = st.number_input("Fee Rate", min_value=0.0, max_value=0.9999, value=float(config["fee_rate"]), format="%.6f")
            interval_seconds = st.number_input("Interval Seconds", min_value=1, value=int(config["interval_seconds"]), step=1)
            cooldown_seconds = st.number_input("Cooldown Seconds", min_value=0, value=int(config["cooldown_seconds"]), step=1)
            live_execution_enabled = st.checkbox("Live Execution Enabled", value=bool(config["live_execution_enabled"]))
            live_auto_entry_enabled = st.checkbox("Live Auto Entry Enabled", value=bool(config.get("live_auto_entry_enabled", False)))
            live_auto_exit_enabled = st.checkbox("Live Auto Exit Enabled", value=bool(config.get("live_auto_exit_enabled", False)))
            live_auto_entry_require_ranking = st.checkbox(
                "Auto Entry Require Ranking",
                value=bool(config.get("live_auto_entry_require_ranking", True)),
            )
            live_auto_entry_rank_resolution = st.selectbox(
                "Auto Entry Rank Resolution",
                ["1", "5", "15", "60", "240", "1D"],
                index=["1", "5", "15", "60", "240", "1D"].index(str(config.get("live_auto_entry_rank_resolution", "240")) if str(config.get("live_auto_entry_rank_resolution", "240")) in {"1", "5", "15", "60", "240", "1D"} else "240"),
            )
            live_auto_entry_rank_lookback_days = st.number_input(
                "Auto Entry Rank Lookback Days",
                min_value=1,
                value=int(config.get("live_auto_entry_rank_lookback_days", 14)),
                step=1,
            )
            live_auto_entry_min_score = st.number_input(
                "Auto Entry Minimum Score",
                min_value=0.0,
                max_value=100.0,
                value=float(config.get("live_auto_entry_min_score", 50.0)),
                step=1.0,
            )
            live_auto_entry_allowed_biases = st.multiselect(
                "Auto Entry Allowed Biases",
                ["bullish", "mixed", "weak"],
                default=[
                    bias
                    for bias in list(config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"]))
                    if bias in {"bullish", "mixed", "weak"}
                ] or ["bullish", "mixed"],
            )
            live_max_order_thb = st.number_input("Live Max Order THB", min_value=1.0, value=float(config["live_max_order_thb"]), step=10.0)
            live_min_thb_balance = st.number_input("Live Min THB Balance", min_value=0.0, value=float(config["live_min_thb_balance"]), step=10.0)
            live_slippage_tolerance_percent = st.number_input(
                "Live Slippage Tolerance %",
                min_value=0.01,
                value=float(config["live_slippage_tolerance_percent"]),
                format="%.2f",
            )
            live_daily_loss_limit_thb = st.number_input("Live Daily Loss Limit THB", min_value=1.0, value=float(config["live_daily_loss_limit_thb"]), step=50.0)
            submitted_system = st.form_submit_button("Save System Settings", use_container_width=True)

        if submitted_system:
            updated = dict(config)
            updated.update(
                {
                    "mode": mode,
                    "base_url": base_url,
                    "fee_rate": float(fee_rate),
                    "interval_seconds": int(interval_seconds),
                    "cooldown_seconds": int(cooldown_seconds),
                    "live_execution_enabled": bool(live_execution_enabled),
                    "live_auto_entry_enabled": bool(live_auto_entry_enabled),
                    "live_auto_exit_enabled": bool(live_auto_exit_enabled),
                    "live_auto_entry_require_ranking": bool(live_auto_entry_require_ranking),
                    "live_auto_entry_rank_resolution": str(live_auto_entry_rank_resolution),
                    "live_auto_entry_rank_lookback_days": int(live_auto_entry_rank_lookback_days),
                    "live_auto_entry_min_score": float(live_auto_entry_min_score),
                    "live_auto_entry_allowed_biases": list(live_auto_entry_allowed_biases) or ["bullish", "mixed"],
                    "live_max_order_thb": float(live_max_order_thb),
                    "live_min_thb_balance": float(live_min_thb_balance),
                    "live_slippage_tolerance_percent": float(live_slippage_tolerance_percent),
                    "live_daily_loss_limit_thb": float(live_daily_loss_limit_thb),
                }
            )
            if _save_config_with_feedback(config, updated, "Saved system settings to config.json"):
                st.rerun()

        st.markdown("#### Retention")
        st.caption("SQLite cleanup policy for stored snapshots, logs, and reconciliation records.")
        with st.form("config_retention_form"):
            market_snapshot_retention_days = st.number_input("Market Snapshots Retention", min_value=1, value=int(config["market_snapshot_retention_days"]), step=1)
            signal_log_retention_days = st.number_input("Signal Logs Retention", min_value=1, value=int(config["signal_log_retention_days"]), step=1)
            runtime_event_retention_days = st.number_input("Runtime Events Retention", min_value=1, value=int(config["runtime_event_retention_days"]), step=1)
            account_snapshot_retention_days = st.number_input("Account Snapshots Retention", min_value=1, value=int(config["account_snapshot_retention_days"]), step=1)
            reconciliation_retention_days = st.number_input("Reconciliation Retention", min_value=1, value=int(config["reconciliation_retention_days"]), step=1)
            submitted_retention = st.form_submit_button("Save Retention", use_container_width=True)
        if submitted_retention:
            updated = dict(config)
            updated.update(
                {
                    "market_snapshot_retention_days": int(market_snapshot_retention_days),
                    "signal_log_retention_days": int(signal_log_retention_days),
                    "runtime_event_retention_days": int(runtime_event_retention_days),
                    "account_snapshot_retention_days": int(account_snapshot_retention_days),
                    "reconciliation_retention_days": int(reconciliation_retention_days),
                }
            )
            if _save_config_with_feedback(config, updated, "Saved retention settings to config.json"):
                st.rerun()

    with right:
        st.markdown("#### Watchlist")
        st.caption("Watchlist symbols drive research, candle sync, and ranking. Live auto-entry still uses config rules only.")
        watchlist_options = market_symbols or sorted(set(watchlist_symbols) | set(configured_symbols))
        with st.form("config_watchlist_form"):
            selected_watchlist = st.multiselect(
                "Watchlist Symbols",
                watchlist_options,
                default=[symbol for symbol in watchlist_symbols if symbol in watchlist_options],
                help="Use this to keep a wider research universe than the live tradable shortlist.",
            )
            watchlist_fallback = st.text_input(
                "Add Watchlist Symbols (comma-separated fallback)",
                value="",
                help="Only needed when the Bitkub market universe is unavailable or you want to paste symbols quickly.",
            )
            submitted_watchlist = st.form_submit_button("Save Watchlist", use_container_width=True)
        if submitted_watchlist:
            extra_symbols = [
                entry.strip().upper()
                for entry in str(watchlist_fallback).split(",")
                if entry.strip()
            ]
            updated = dict(config)
            updated["watchlist_symbols"] = sorted(set(selected_watchlist) | set(extra_symbols) | set(configured_symbols))
            if _save_config_with_feedback(config, updated, "Saved watchlist symbols"):
                st.rerun()

        st.markdown("#### Telegram Foundation")
        st.caption("Telegram notifications use TELEGRAM_BOT_TOKEN plus TELEGRAM_CHAT_IDS or TELEGRAM_CHAT_ID from .env. Telegram commands are allowed only for TELEGRAM_ALLOWED_CHAT_IDS when set; otherwise they fall back to the notify chat ids.")
        telegram_notify_defaults = [
            event_name
            for event_name in config.get("telegram_notify_events", DEFAULT_TELEGRAM_NOTIFY_EVENTS)
            if event_name in DEFAULT_TELEGRAM_NOTIFY_EVENTS
        ]
        with st.form("config_telegram_form"):
            telegram_enabled = st.checkbox("Telegram Notifications Enabled", value=bool(config.get("telegram_enabled", False)))
            telegram_control_enabled = st.checkbox("Telegram Control Enabled", value=bool(config.get("telegram_control_enabled", False)))
            telegram_notify_events = st.multiselect(
                "Notify Events",
                DEFAULT_TELEGRAM_NOTIFY_EVENTS,
                default=telegram_notify_defaults or DEFAULT_TELEGRAM_NOTIFY_EVENTS,
            )
            submitted_telegram = st.form_submit_button("Save Telegram Settings", use_container_width=True)
        if submitted_telegram:
            updated = dict(config)
            updated["telegram_enabled"] = bool(telegram_enabled)
            updated["telegram_control_enabled"] = bool(telegram_control_enabled)
            updated["telegram_notify_events"] = [str(event_name) for event_name in telegram_notify_events]
            if _save_config_with_feedback(config, updated, "Saved Telegram foundation settings"):
                st.rerun()

        st.markdown("#### Manual Live Order Preset")
        st.caption("This preset is used by manual live execution actions, not by the auto loop.")
        symbols = configured_symbols
        manual_order = dict(config.get("live_manual_order", {}))
        default_symbol = str(manual_order.get("symbol", symbols[0]))
        with st.form("config_manual_order_form"):
            mo_enabled = st.checkbox("Enabled", value=bool(manual_order.get("enabled", False)))
            mo_symbol = st.selectbox(
                "Symbol",
                symbols,
                index=max(0, symbols.index(default_symbol)) if default_symbol in symbols else 0,
            )
            mo_side = st.selectbox("Side", ["buy", "sell"], index=0 if manual_order.get("side", "buy") == "buy" else 1)
            mo_order_type = st.selectbox("Order Type", ["limit"], index=0)
            mo_amount_thb = st.number_input("Amount THB", min_value=0.01, value=float(manual_order.get("amount_thb", 100.0)), step=10.0)
            mo_amount_coin = st.number_input("Amount Coin", min_value=0.00000001, value=float(manual_order.get("amount_coin", 0.0001)), format="%.8f")
            mo_rate = st.number_input("Rate", min_value=0.00000001, value=float(manual_order.get("rate", 1.0)), format="%.8f")
            submitted_manual_order = st.form_submit_button("Save Manual Order Preset", use_container_width=True)
        if submitted_manual_order:
            updated = dict(config)
            updated["live_manual_order"] = {
                "enabled": bool(mo_enabled),
                "symbol": mo_symbol,
                "side": mo_side,
                "order_type": mo_order_type,
                "amount_thb": float(mo_amount_thb),
                "amount_coin": float(mo_amount_coin),
                "rate": float(mo_rate),
            }
            if _save_config_with_feedback(config, updated, "Saved manual live order preset"):
                st.rerun()

        st.markdown("#### Rules Editor")
        st.caption("Edit one symbol at a time. Changes are saved back into the shared config file.")
        selected_rule_symbol = st.selectbox("Rule Symbol", symbols, key="selected_rule_symbol")
        rule = dict(config["rules"][selected_rule_symbol])
        st.markdown(
            " ".join(
                [
                    badge(f"buy <= {float(rule['buy_below']):,.8f}", "info"),
                    badge(f"sell >= {float(rule['sell_above']):,.8f}", "good"),
                    badge(f"budget {float(rule['budget_thb']):,.2f} THB", "warn"),
                ]
            ),
            unsafe_allow_html=True,
        )
        with st.form("config_rule_editor_form"):
            buy_below = st.number_input("Buy Below", min_value=0.00000001, value=float(rule["buy_below"]), format="%.8f")
            sell_above = st.number_input("Sell Above", min_value=0.00000001, value=float(rule["sell_above"]), format="%.8f")
            budget_thb = st.number_input("Budget THB", min_value=0.01, value=float(rule["budget_thb"]), step=10.0)
            stop_loss_percent = st.number_input("Stop Loss %", min_value=0.01, value=float(rule["stop_loss_percent"]), format="%.4f")
            take_profit_percent = st.number_input("Take Profit %", min_value=0.01, value=float(rule["take_profit_percent"]), format="%.4f")
            max_trades_per_day = st.number_input("Max Trades Per Day", min_value=1, value=int(rule["max_trades_per_day"]), step=1)
            submitted_rule = st.form_submit_button("Save Rule", use_container_width=True)
        if submitted_rule:
            updated = dict(config)
            updated_rules = dict(config["rules"])
            updated_rules[selected_rule_symbol] = {
                "buy_below": float(buy_below),
                "sell_above": float(sell_above),
                "budget_thb": float(budget_thb),
                "stop_loss_percent": float(stop_loss_percent),
                "take_profit_percent": float(take_profit_percent),
                "max_trades_per_day": int(max_trades_per_day),
            }
            updated["rules"] = updated_rules
            if _save_config_with_feedback(config, updated, f"Saved rule for {selected_rule_symbol}"):
                st.rerun()

        st.markdown("#### Add / Remove Rule")
        st.caption("Use this section only for symbol lifecycle changes. It is riskier than normal rule edits.")
        if market_universe.get("error"):
            st.warning(f"Bitkub market symbols unavailable right now: {market_universe['error']}")
            new_symbol = st.text_input(
                "New Symbol",
                value="",
                help="Fallback manual entry when the Bitkub market-symbol endpoint is unavailable.",
            ).strip().upper()
        elif market_symbols:
            st.caption(
                f"Bitkub market symbols loaded: {len(market_symbols)} | already configured: {len(configured_symbols)} | available to add: {len(available_new_symbols)}"
            )
            if available_new_symbols:
                new_symbol = st.selectbox(
                    "New Symbol",
                    available_new_symbols,
                    help="Symbols already configured are hidden from this list.",
                    key="config_new_rule_symbol",
                )
            else:
                new_symbol = ""
                st.info("All currently loaded Bitkub market symbols are already present in bot rules.")
        else:
            new_symbol = ""
            st.info("No Bitkub market symbols were returned right now.")
        add_col, remove_col = st.columns(2)
        with add_col:
            if st.button("Add New Rule", use_container_width=True):
                if not new_symbol:
                    st.error("No market symbol is available to add right now.")
                elif new_symbol in config["rules"]:
                    st.error("That symbol already exists in rules.")
                else:
                    updated = dict(config)
                    updated_rules = dict(config["rules"])
                    updated_rules[new_symbol] = _build_rule_seed(config, new_symbol)
                    updated["rules"] = updated_rules
                    updated["watchlist_symbols"] = sorted(set(config.get("watchlist_symbols", configured_symbols)) | {new_symbol})
                    if _save_config_with_feedback(config, updated, f"Added new rule {new_symbol}"):
                        st.rerun()
        with remove_col:
            confirm_remove = st.checkbox("Confirm remove selected rule", key="confirm_remove_rule")
            if st.button("Remove Selected Rule", use_container_width=True):
                if not confirm_remove:
                    st.error("Tick confirm before removing a rule.")
                elif len(config["rules"]) <= 1:
                    st.error("At least one rule must remain in config.")
                else:
                    updated = dict(config)
                    updated_rules = dict(config["rules"])
                    updated_rules.pop(selected_rule_symbol, None)
                    updated["rules"] = updated_rules
                    updated["watchlist_symbols"] = sorted(set(config.get("watchlist_symbols", [])) | set(updated_rules.keys()))
                    if _save_config_with_feedback(config, updated, f"Removed rule {selected_rule_symbol}"):
                        st.rerun()

    with st.expander("Raw Config Preview", expanded=False):
        st.json(config, expanded=False)
