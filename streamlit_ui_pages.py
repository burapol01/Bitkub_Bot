from __future__ import annotations

from typing import Any

import streamlit as st

from config import CONFIG_PATH, save_config, summarize_config_changes
from services.account_service import build_live_holdings_snapshot
from services.db_service import (
    DB_PATH,
    fetch_dashboard_summary,
    fetch_db_maintenance_summary,
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_reporting_summary,
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
from streamlit_ui_actions import (
    persist_execution_order_update,
    submit_manual_order_from_ui,
)
from streamlit_ui_data import calc_daily_totals
from streamlit_ui_refresh import PAGE_ORDER
from streamlit_ui_styles import badge, render_metric_card
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
                open_rows.append({"symbol": symbol, "status": "ERROR", "open_orders": entry.get("error")})

    left, right = st.columns([1.15, 0.85])
    with left:
        st.markdown('<div class="panel-title">Live Holdings</div>', unsafe_allow_html=True)
        st.dataframe(holdings, use_container_width=True, hide_index=True)
    with right:
        st.markdown('<div class="panel-title">Exchange Open Orders</div>', unsafe_allow_html=True)
        st.dataframe(open_rows, use_container_width=True, hide_index=True)


def render_live_ops_page(
    *,
    config: dict[str, Any],
    runtime: dict[str, Any],
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
) -> None:
    client = private_ctx["client"]
    account_snapshot = private_ctx["account_snapshot"]
    if client is None or account_snapshot is None:
        st.warning("Private API is required for live operations.")
        return

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
    open_execution_orders = execution_summary["open_orders"]
    recent_execution_orders = execution_summary["recent_orders"]
    recent_execution_events = execution_summary["recent_events"]
    available_balances = extract_available_balances(account_snapshot)
    thb_available = float(available_balances.get("THB", 0.0))
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
            f"{thb_available:,.2f}",
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
                        badge(str(last_execution["state"]).upper(), "good" if str(last_execution["state"]) in {"filled", "canceled"} else "info"),
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
            current_focus = st.selectbox("Selected Open Order", list(focus_options.keys()), key="live_ops_selected_order")
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

    left, right = st.columns([1.1, 0.9])
    with left:
        st.markdown('<div class="panel-title">Manual Live Order</div>', unsafe_allow_html=True)
        symbols = sorted(config["rules"].keys())
        manual_defaults = dict(config.get("live_manual_order", {}))
        default_symbol = str(manual_defaults.get("symbol", symbols[0]))
        with st.form("manual_live_order_form"):
            symbol = st.selectbox(
                "Symbol",
                symbols,
                index=max(0, symbols.index(default_symbol)) if default_symbol in symbols else 0,
            )
            side = st.selectbox("Side", ["buy", "sell"], index=0 if manual_defaults.get("side", "buy") == "buy" else 1)
            order_type = st.selectbox("Order Type", ["limit"], index=0)
            amount_thb = st.number_input("Amount THB", min_value=0.0, value=float(manual_defaults.get("amount_thb", 100.0)), step=10.0)
            amount_coin = st.number_input("Amount Coin", min_value=0.0, value=float(manual_defaults.get("amount_coin", 0.0)), format="%.8f")
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

        st.markdown(
            '<div class="panel-title">Pre-flight Checks</div>',
            unsafe_allow_html=True,
        )
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
            selected_label = st.selectbox("Cancel Target", list(option_map.keys()))
            confirm_cancel = st.checkbox("Confirm live cancel request")
            if st.button("Cancel Selected Order", use_container_width=True):
                if not confirm_cancel:
                    st.error("Tick confirm before canceling a live order.")
                else:
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
                            details={"execution_order_id": int(target_order["id"])},
                        )
                    except Exception as e:
                        _set_live_ops_feedback(
                            "Live cancel failed",
                            [str(e)],
                            tone="error",
                        )
                        st.error(str(e))
                    else:
                        _set_live_ops_feedback(
                            "Live cancel completed",
                            [
                                f"id={target_order['id']} | symbol={target_order['symbol']}",
                                f"new_state={canceled_record['state']}",
                            ],
                        )
                        st.success(f"Order id={target_order['id']} is now {canceled_record['state']}.")
                        st.rerun()
        else:
            st.caption("No open live orders are available for cancel.")

        with st.expander("Guardrails JSON", expanded=False):
            st.json(guardrails, expanded=False)

    history_left, history_right = st.columns([1.0, 1.0])
    with history_left:
        st.markdown('<div class="panel-title">Recent Execution Orders</div>', unsafe_allow_html=True)
        st.dataframe(recent_execution_orders, use_container_width=True, hide_index=True)
    with history_right:
        st.markdown('<div class="panel-title">Recent Execution Events</div>', unsafe_allow_html=True)
        st.dataframe(recent_execution_events, use_container_width=True, hide_index=True)


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
        st.dataframe(symbol_summary, use_container_width=True, hide_index=True)
    with right:
        st.markdown('<div class="panel-title">Recent Paper Trades</div>', unsafe_allow_html=True)
        st.dataframe(recent_trades, use_container_width=True, hide_index=True)

    bottom_left, bottom_right = st.columns([1.0, 1.0])
    with bottom_left:
        st.markdown('<div class="panel-title">Recent Execution Orders</div>', unsafe_allow_html=True)
        st.dataframe(recent_execution_orders, use_container_width=True, hide_index=True)
        st.markdown('<div class="panel-title">Recent Auto Exit Events</div>', unsafe_allow_html=True)
        st.dataframe(recent_auto_exit_events, use_container_width=True, hide_index=True)
    with bottom_right:
        st.markdown('<div class="panel-title">Recent Runtime Errors</div>', unsafe_allow_html=True)
        st.dataframe(recent_errors, use_container_width=True, hide_index=True)


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
        render_metric_card("Rules", str(len(config["rules"])), f"Interval {int(config['interval_seconds'])}s")
    with summary_col3:
        render_metric_card(
            "Live Controls",
            "ON" if bool(config["live_execution_enabled"]) else "OFF",
            "Auto exit ON" if bool(config.get("live_auto_exit_enabled", False)) else "Auto exit OFF",
        )
    with summary_col4:
        render_metric_card(
            "Manual Preset",
            str(config.get("live_manual_order", {}).get("symbol", "n/a")),
            str(config.get("live_manual_order", {}).get("side", "n/a")).upper(),
        )

    with st.expander("Rule Summary", expanded=False):
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
            live_auto_exit_enabled = st.checkbox("Live Auto Exit Enabled", value=bool(config.get("live_auto_exit_enabled", False)))
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
                    "live_auto_exit_enabled": bool(live_auto_exit_enabled),
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
        st.markdown("#### Manual Live Order Preset")
        st.caption("This preset is used by manual live execution actions, not by the auto loop.")
        symbols = sorted(config["rules"].keys())
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
        new_symbol = st.text_input("New Symbol", value="", help="Example: THB_XRP").strip().upper()
        add_col, remove_col = st.columns(2)
        with add_col:
            if st.button("Add New Rule", use_container_width=True):
                if not new_symbol:
                    st.error("Enter a symbol before adding a new rule.")
                elif new_symbol in config["rules"]:
                    st.error("That symbol already exists in rules.")
                else:
                    updated = dict(config)
                    updated_rules = dict(config["rules"])
                    updated_rules[new_symbol] = {
                        "buy_below": 1.0,
                        "sell_above": 1.1,
                        "budget_thb": 100.0,
                        "stop_loss_percent": 1.0,
                        "take_profit_percent": 1.2,
                        "max_trades_per_day": 1,
                    }
                    updated["rules"] = updated_rules
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
                    if _save_config_with_feedback(config, updated, f"Removed rule {selected_rule_symbol}"):
                        st.rerun()

    with st.expander("Raw Config Preview", expanded=False):
        st.json(config, expanded=False)
