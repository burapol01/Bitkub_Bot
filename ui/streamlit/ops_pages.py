from __future__ import annotations

from typing import Any

import streamlit as st

from config import load_config, ordered_unique_symbols
from services.account_service import (
    build_live_holdings_snapshot,
    fetch_open_orders_by_symbol_snapshot,
)
from services.db_service import (
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_overview_summary,
)
from services.execution_service import (
    LiveExecutionGuardrailError,
    build_live_execution_guardrails,
    cancel_live_order,
    refresh_live_order_from_exchange,
)
from services.reconciliation_service import extract_available_balances
from ui.streamlit.actions import (
    persist_execution_order_update,
    submit_manual_order_from_ui,
)
from ui.streamlit.data import calc_daily_totals, capability_badge_tone
from ui.streamlit.refresh import render_refreshable_fragment
from ui.streamlit.styles import badge, render_callout, render_metric_card, render_section_intro
from ui.streamlit.strategy_support import evaluate_fee_guardrail
from utils.time_utils import now_text


@st.cache_data(ttl=10, show_spinner=False)
def _cached_overview_summary(today: str) -> dict[str, Any]:
    return fetch_overview_summary(today=today)


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



def render_overview_page(
    *,
    config: dict[str, Any],
    runtime: dict[str, Any],
    ticker_rows: list[dict[str, Any]],
    private_ctx: dict[str, Any],
    today: str,
) -> None:
    paper_trades_today, paper_wins_today, paper_losses_today, paper_realized_today = calc_daily_totals(runtime["daily_stats"])
    overview_summary = _cached_overview_summary(today)
    live_execution_pnl = dict(overview_summary.get("live_execution_pnl") or {})
    paper_total_realized = float((overview_summary.get("paper_trades") or {}).get("total_realized_pnl", 0.0) or 0.0)
    live_total_realized = float(live_execution_pnl.get("total_realized_pnl", 0.0) or 0.0)
    combined_total_realized = paper_total_realized + live_total_realized
    paper_total_fee = float((overview_summary.get("paper_trades") or {}).get("total_fee_thb", 0.0) or 0.0)
    live_total_fee = float(live_execution_pnl.get("total_fee_thb", 0.0) or 0.0)
    combined_total_fee = paper_total_fee + live_total_fee
    paper_total_trades = int((overview_summary.get("paper_trades") or {}).get("total", 0) or 0)
    live_total_trades = int(live_execution_pnl.get("total", 0) or 0)
    paper_fee_today = float((overview_summary.get("paper_trades") or {}).get("today_fee_thb", 0.0) or 0.0)
    live_fee_today = float(live_execution_pnl.get("today_fee_thb", 0.0) or 0.0)
    combined_fee_today = paper_fee_today + live_fee_today
    live_realized_today = float(live_execution_pnl.get("today_realized_pnl", 0.0) or 0.0)
    live_trades_today = int(live_execution_pnl.get("today", 0) or 0)
    live_wins_today = int(live_execution_pnl.get("today_wins", 0) or 0)
    live_losses_today = int(live_execution_pnl.get("today_losses", 0) or 0)
    combined_realized_today = paper_realized_today + live_realized_today
    combined_trades_today = int(paper_trades_today) + int(live_trades_today)
    combined_avg_pnl_today = (combined_realized_today / combined_trades_today) if combined_trades_today else 0.0
    combined_avg_fee_today = (combined_fee_today / combined_trades_today) if combined_trades_today else 0.0
    fee_drag_reference = combined_realized_today + combined_fee_today if combined_realized_today > 0 else 0.0
    combined_fee_drag_today = (combined_fee_today * 100.0 / fee_drag_reference) if fee_drag_reference > 0 else 0.0
    fee_guardrail, fee_guardrail_note, _ = evaluate_fee_guardrail(
        trades=combined_trades_today,
        total_pnl_thb=combined_realized_today,
        total_fee_thb=combined_fee_today,
        avg_pnl_thb=combined_avg_pnl_today,
        avg_fee_thb=combined_avg_fee_today,
        fee_drag_percent=combined_fee_drag_today,
    )

    render_section_intro(
        "Overview",
        "Start here for the daily outcome, current market context, and control health before drilling into details.",
        "Control Surface",
    )

    status_badges = [
        badge(f"Mode {str(config['mode']).upper()}", "info"),
        badge("Manual Pause ON" if runtime["manual_pause"] else "Manual Pause OFF", "warn" if runtime["manual_pause"] else "good"),
        badge("Private API READY" if private_ctx["account_snapshot"] is not None else "Private API OFFLINE", "good" if private_ctx["account_snapshot"] is not None else "bad"),
        badge(f"Fee {fee_guardrail}", "bad" if fee_guardrail == "LOSS_AFTER_FEES" else "warn" if fee_guardrail in {"FEE_HEAVY", "THIN_EDGE"} else "good"),
    ]
    st.markdown(f'<div class="status-strip">{" ".join(status_badges)}</div>', unsafe_allow_html=True)

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        render_metric_card("Trading Mode", str(config["mode"]).upper(), private_ctx["private_api_status"])
    with col2:
        render_metric_card("Rules", str(len(config["rules"])), f"Open paper positions {len(runtime['positions'])}")
    with col3:
        render_metric_card(
            "Daily Stats Symbols",
            str(len(runtime["daily_stats"].get(today, {}))),
            f"Cooldowns {len(runtime['cooldowns'])}",
        )
    with col4:
        render_metric_card(
            "Realized Today",
            f"{combined_realized_today:,.2f} THB",
            f"Paper {paper_realized_today:,.2f} | Live {live_realized_today:,.2f}",
        )
    with col5:
        render_metric_card(
            "Realized Since Start",
            f"{combined_total_realized:,.2f} THB",
            f"Paper {paper_total_realized:,.2f} | Live {live_total_realized:,.2f}",
        )

    render_section_intro("Market Overview", "Live prices against the current entry/exit zones for each configured rule.", "Market")
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
        width='stretch',
        hide_index=True,
    )

    pnl_left, pnl_right = st.columns([1.0, 1.0])
    with pnl_left:
        render_section_intro("Realized PnL Breakdown", "Combined paper and live outcomes for the current day, including fee drag.", "P&L")
        pnl_cards = st.columns(5)
        with pnl_cards[0]:
            render_metric_card("Combined Today", f"{combined_realized_today:,.2f} THB", f"{today}")
        with pnl_cards[1]:
            render_metric_card(
                "Paper Today",
                f"{paper_realized_today:,.2f} THB",
                f"Trades {paper_trades_today} | W {paper_wins_today} / L {paper_losses_today}",
            )
        with pnl_cards[2]:
            render_metric_card(
                "Live Today",
                f"{live_realized_today:,.2f} THB",
                f"Trades {live_trades_today} | W {live_wins_today} / L {live_losses_today}",
            )
        with pnl_cards[3]:
            render_metric_card(
                "Fee Today",
                f"{combined_fee_today:,.2f} THB",
                f"Paper {paper_fee_today:,.2f} | Live {live_fee_today:,.2f}",
            )
        with pnl_cards[4]:
            render_metric_card(
                "Combined Since Start",
                f"{combined_total_realized:,.2f} THB",
                f"Trades {paper_total_trades + live_total_trades} | Fee {combined_total_fee:,.2f}",
            )
        st.caption(
            "Paper realized comes from runtime daily_stats/paper trade flow. Live realized is estimated from filled execution_orders using FIFO cost basis across filled buy/sell orders. Fee Today combines paper_trade_logs fees with filled live execution fees."
        )
        render_callout(
            "Fee Watch",
            f"{fee_guardrail} | {fee_guardrail_note}",
            "bad" if fee_guardrail == "LOSS_AFTER_FEES" else "warn" if fee_guardrail in {"FEE_HEAVY", "THIN_EDGE"} else "good",
        )
    with pnl_right:
        render_section_intro("Overview Notes", "Read this block when a number looks surprising or you need to understand what is connected already.", "Context")
        notes = [
            f"Mode: {str(config['mode']).upper()} | live execution {'ON' if bool(config.get('live_execution_enabled', False)) else 'OFF'}",
            f"Auto entry {'ON' if bool(config.get('live_auto_entry_enabled', False)) else 'OFF'} | auto exit {'ON' if bool(config.get('live_auto_exit_enabled', False)) else 'OFF'}",
            f"Private API status: {private_ctx['private_api_status']}",
            "Realized Today now combines paper and live execution, but the live side is an execution-order estimate rather than a full exchange ledger.",
            "Fee Today combines paper_trade_logs fees with filled execution-order fees so you can gauge the cash drag of the strategy each day.",
            f"Fee guardrail today: {fee_guardrail} | fee drag {combined_fee_drag_today:.2f}% | trades {combined_trades_today}",
        ]
        for note in notes:
            st.caption(note)

    status_left, status_right = st.columns([1.05, 0.95])
    with status_left:
        render_section_intro("Control Snapshot", "Runtime, private API, and execution-readiness signals condensed into one place.", "Status")
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
                badge(item, capability_badge_tone(item))
                for item in private_ctx["private_api_capabilities"]
            ),
            unsafe_allow_html=True,
        )
        st.caption(private_ctx["private_api_status"])
    with status_right:
        render_section_intro("Latest Execution", "The newest execution-order state recorded by the engine.", "Execution")
        latest_execution = overview_summary.get("latest_execution_order")
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
        render_callout(
            "Runtime Restore",
            "<br>".join(runtime["messages"]),
            "info",
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

    render_section_intro(
        "Account",
        "Start with capability and wallet summary, then move to holdings and open orders only if something looks off.",
        "Wallet & Orders",
    )
    capability_tones = [
        capability_badge_tone(item)
        for item in private_ctx["private_api_capabilities"]
    ]
    status_badges = [
        badge("Account Snapshot READY", "good"),
        badge("Capability PARTIAL" if any(tone == "warn" for tone in capability_tones) else "Capability OK", "warn" if any(tone == "warn" for tone in capability_tones) else "good"),
        badge("Capability ERROR" if any(tone == "bad" for tone in capability_tones) else "No hard errors", "bad" if any(tone == "bad" for tone in capability_tones) else "good"),
    ]
    st.markdown(f'<div class="status-strip">{" ".join(status_badges)}</div>', unsafe_allow_html=True)
    render_section_intro("Capability Matrix", "Private API readiness, open-order support, and partial capability signals live here.", "Status")
    st.markdown(
        " ".join(
            badge(item, capability_badge_tone(item))
            for item in private_ctx["private_api_capabilities"]
        ),
        unsafe_allow_html=True,
    )
    if private_ctx["errors"]:
        render_callout(
            "Account Warnings",
            "<br>".join(str(error) for error in private_ctx["errors"]),
            "warn",
        )
    else:
        render_callout(
            "Account Snapshot",
            "Private API snapshot loaded without account-level warnings.",
            "good",
        )

    holdings = build_live_holdings_snapshot(
        account_snapshot=account_snapshot,
        latest_prices=latest_prices,
        latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
    )
    total_holdings_value = sum(float(row.get("market_value_thb", 0.0)) for row in holdings)
    reserved_rows = sum(
        1 for row in holdings if float(row.get("reserved_qty", 0.0)) > 0
    )

    def _build_open_order_rows(open_order_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not isinstance(open_order_snapshot, dict):
            return rows

        for symbol, entry in sorted(open_order_snapshot.items()):
            if not isinstance(entry, dict):
                continue
            if entry.get("ok", False):
                payload = entry.get("data", {})
                payload_rows = payload.get("result", payload) if isinstance(payload, dict) else payload
                count = len(payload_rows) if isinstance(payload_rows, list) else 0
                rows.append(
                    {
                        "symbol": symbol,
                        "status": "OK",
                        "open_orders": str(count),
                        "detail": "",
                    }
                )
            else:
                error_message = str(entry.get("error") or "")
                if "Endpoint not found for path /api/market/my-open-orders" in error_message or "Endpoint not found for path /api/v3/market/my-open-orders" in error_message:
                    rows.append(
                        {
                            "symbol": symbol,
                            "status": "UNSUPPORTED",
                            "open_orders": "n/a",
                            "detail": "my-open-orders is unsupported for this symbol",
                        }
                    )
                else:
                    rows.append(
                        {
                            "symbol": symbol,
                            "status": "ERROR",
                            "open_orders": "n/a",
                            "detail": error_message,
                        }
                    )
        return rows

    open_orders_meta = dict(account_snapshot.get("open_orders_meta") or {})
    requires_symbol_probe = bool(open_orders_meta.get("requires_symbol"))
    open_orders = account_snapshot.get("open_orders", {})
    configured_symbols = sorted(load_config().get("rules", {}).keys())
    default_probe_symbols = ordered_unique_symbols(
        [
            str(order.get("symbol", ""))
            for order in fetch_open_execution_orders()
            if str(order.get("symbol", "")).strip()
        ],
        [
            str(row.get("symbol", ""))
            for row in holdings
            if str(row.get("symbol", "")) != "THB"
        ],
    )[:12]

    if requires_symbol_probe:
        with st.expander("Per-Symbol Open Order Probe", expanded=bool(st.session_state.get("account_open_order_probe_results"))):
            st.caption(
                "This Bitkub API path does not support a global open-orders query. Probe specific symbols here to see which coin returns an unsupported or error state."
            )
            with st.form("account_open_order_probe_form"):
                probe_symbols = st.multiselect(
                    "Probe Symbols",
                    configured_symbols,
                    default=[symbol for symbol in default_probe_symbols if symbol in configured_symbols],
                    help="Start with current holdings or open execution-order symbols, then widen if needed.",
                )
                run_probe = st.form_submit_button(
                    "Probe Selected Symbols",
                    width='stretch',
                )
            if run_probe:
                if not probe_symbols:
                    st.warning("Select at least one symbol before probing open orders.")
                elif private_ctx.get("client") is None:
                    st.error("Private API client is unavailable for per-symbol open-order probes.")
                else:
                    st.session_state["account_open_order_probe_results"] = fetch_open_orders_by_symbol_snapshot(
                        private_ctx["client"],
                        symbols=list(probe_symbols),
                    )

    open_rows = _build_open_order_rows(open_orders)
    probe_results = st.session_state.get("account_open_order_probe_results", {})
    if requires_symbol_probe and isinstance(probe_results, dict) and probe_results:
        open_rows = _build_open_order_rows(probe_results)

    unsupported_open_order_symbols = sum(1 for row in open_rows if str(row.get("status")) == "UNSUPPORTED")
    error_open_order_symbols = sum(1 for row in open_rows if str(row.get("status")) == "ERROR")
    open_rows.sort(
        key=lambda row: (
            {"ERROR": 0, "UNSUPPORTED": 1, "OK": 2}.get(str(row.get("status")), 9),
            str(row.get("symbol") or ""),
        )
    )
    error_symbol_labels = [str(row.get("symbol") or "n/a") for row in open_rows if str(row.get("status")) == "ERROR"]

    metric1, metric2, metric3 = st.columns(3)
    with metric1:
        render_metric_card("Holdings Rows", str(len(holdings)), f"Reserved rows {reserved_rows}")
    with metric2:
        render_metric_card("Total Holding Value", f"{total_holdings_value:,.2f} THB", "Mark-to-market estimate")
    with metric3:
        render_metric_card(
            "Open Order Symbols",
            str(len(open_rows)) if open_rows else ("Probe needed" if requires_symbol_probe else "0"),
            "Per-symbol probe pending" if requires_symbol_probe and not open_rows else "Exchange snapshot summary",
        )

    if requires_symbol_probe and not open_rows:
        render_callout(
            "Open Order Coverage",
            "Global open-orders is unsupported by this Bitkub API path. Use the per-symbol probe above to identify the specific coin(s) that need handling.",
            "info",
        )
    elif error_open_order_symbols:
        render_callout(
            "Open Order Coverage",
            f"{error_open_order_symbols} symbol(s) returned hard open-order errors: {', '.join(error_symbol_labels)}. Check the right-hand table before trusting the exchange snapshot.",
            "bad",
        )
    elif unsupported_open_order_symbols:
        render_callout(
            "Open Order Coverage",
            f"{unsupported_open_order_symbols} symbol(s) are marked unsupported for my-open-orders. This is usually safe if you already know they are broker-coin style symbols.",
            "warn",
        )
    else:
        render_callout(
            "Open Order Coverage",
            "All tracked symbols returned a clean open-order capability snapshot.",
            "good",
        )

    left, right = st.columns([1.15, 0.85])
    with left:
        render_section_intro("Live Holdings", "Mark-to-market holdings using the latest known prices and filled execution history.", "Holdings")
        if holdings:
            st.dataframe(holdings, width='stretch', hide_index=True)
        else:
            st.caption("No live holdings found in the current account snapshot.")
    with right:
        render_section_intro("Exchange Open Orders", "Capability-normalized open-order snapshot from the exchange. Unsupported symbols stay visible here so they are easy to spot.", "Orders")
        if open_rows:
            st.dataframe(open_rows, width='stretch', hide_index=True)
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

        render_section_intro(
            "Live Ops",
            "Use this page for real actions only. Read the summary first, then move left to the order form and right to live controls.",
            "Execution",
        )

        status_badges = [
            badge("Execution READY" if guardrails.get("ready") else "Execution BLOCKED", "good" if guardrails.get("ready") else "bad"),
            badge("Auto Exit ON" if guardrails.get("live_auto_exit_enabled") else "Auto Exit OFF", "info"),
            badge("Auto Entry ON" if guardrails.get("live_auto_entry_enabled") else "Auto Entry OFF", "info"),
            badge(f"Open Orders {len(open_execution_orders)}", "warn" if open_execution_orders else "good"),
        ]
        st.markdown(f'<div class="status-strip">{" ".join(status_badges)}</div>', unsafe_allow_html=True)

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

        if guardrails.get("blocked_reasons"):
            render_callout(
                "Guardrail Snapshot",
                "<br>".join(str(reason) for reason in guardrails["blocked_reasons"]),
                "warn",
            )
        else:
            render_callout(
                "Guardrail Snapshot",
                "No blocking reasons right now. The engine can still reject the final submit if the exchange response changes.",
                "good",
            )

        _show_live_ops_feedback()

        summary_left, summary_right = st.columns([0.95, 1.05])
        with summary_left:
            render_section_intro("Latest Action Snapshot", "The newest execution update recorded by the engine.", "Action")
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
            render_section_intro("Open Order Focus", "Inspect the currently selected open execution order before refreshing or canceling it.", "Focus")
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
        render_section_intro("Manual Live Order", "Submit a real order only after the pre-flight checks look clean.", "Action Form")
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
            submitted = st.form_submit_button("Submit Manual Order", type="primary", width='stretch')

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

        render_section_intro("Pre-flight Checks", "This checks internal consistency only. Exchange responses and live guardrails still decide the final outcome.", "Validation")
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

            render_section_intro("Live Controls", "Use refresh and cancel here. Keep this side focused on open-order maintenance, not new order creation.", "Controls")
            if st.button("Refresh Open Live Orders", width='stretch'):
                refreshed = 0
                for order in open_execution_orders:
                    refreshed_record, events = refresh_live_order_from_exchange(
                        client=client,
                        order_record=order,
                        occurred_at=now_text(),
                    )
                    persist_execution_order_update(int(order["id"]), refreshed_record, events)
                    refreshed += 1
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
                if st.button("Cancel Selected Orders", width='stretch'):
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
            render_section_intro("Recent Execution Orders", "Latest order states first. Use this together with the events table to understand what the engine just did.", "History")
            st.dataframe(recent_execution_orders, width='stretch', hide_index=True)
        with history_right:
            render_section_intro("Recent Execution Events", "Execution event stream behind the latest orders.", "History")
            st.dataframe(recent_execution_events, width='stretch', hide_index=True)

    render_refreshable_fragment(auto_refresh_run_every, _render_live_ops_history)
