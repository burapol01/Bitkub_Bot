from __future__ import annotations

from typing import Any

import streamlit as st

from config import load_config, ordered_unique_symbols
from services.account_service import (
    build_live_holdings_snapshot,
    extract_open_order_rows,
    fetch_open_orders_by_symbol_snapshot,
    probe_open_orders_support_snapshot,
)
from services.db_service import (
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_overview_summary,
    fetch_recent_trade_journal,
)
from services.execution_service import (
    LiveExecutionGuardrailError,
    build_exit_guardrail_resolution,
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
from ui.streamlit.strategy_support import evaluate_fee_guardrail, fetch_market_symbol_universe
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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _is_sell_slippage_guardrail_message(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    return (
        "sell rate deviates" in normalized
        and "live_slippage_tolerance_percent" in normalized
    )


def _latest_auto_exit_slippage_block_row(
    *,
    limit: int = 40,
) -> dict[str, Any] | None:
    rows = fetch_recent_trade_journal(
        limit=limit,
        channel="auto_live_exit",
        status="blocked",
    )
    for row in rows:
        details = dict(row.get("details") or {})
        errors = [str(line) for line in list(details.get("errors") or [])]
        if any(_is_sell_slippage_guardrail_message(line) for line in errors):
            return row
    return None


def _queue_manual_one_time_sell_prefill(
    *,
    symbol: str,
    amount_coin: float,
    rate: float,
) -> None:
    st.session_state["live_ops_manual_order_prefill"] = {
        "symbol": str(symbol),
        "side": "sell",
        "order_type": "limit",
        "amount_coin": float(amount_coin),
        "rate": float(rate),
        "confirm": False,
    }


def _open_strategy_workspace_for_symbol(
    *,
    symbol: str,
    workspace: str,
) -> None:
    st.session_state["ui_page"] = "Strategy"
    st.query_params["page"] = "Strategy"
    st.session_state["strategy_workspace"] = str(workspace)

    if workspace == "Live Tuning":
        st.session_state["strategy_tuning_focus_symbol"] = str(symbol)
    elif workspace == "Compare":
        compare_source = str(st.session_state.get("strategy_compare_source", "candles"))
        compare_resolution = str(st.session_state.get("strategy_compare_resolution", "240"))
        compare_days = int(st.session_state.get("strategy_compare_days", 14) or 14)
        st.session_state["strategy_compare_symbol"] = str(symbol)
        st.session_state["strategy_compare_symbol__input"] = str(symbol)
        st.session_state["strategy_compare_autorun"] = {
            "symbol": str(symbol),
            "source": compare_source,
            "resolution": compare_resolution,
            "days": compare_days,
        }

    st.rerun()



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
    market_universe = fetch_market_symbol_universe()
    market_source_by_symbol = {
        str(symbol): str(source)
        for symbol, source in dict(market_universe.get("source_by_symbol") or {}).items()
        if str(symbol)
    }
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
                count = len(extract_open_order_rows(payload))
                rows.append(
                    {
                        "symbol": symbol,
                        "source": market_source_by_symbol.get(symbol, "unknown"),
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
                            "source": market_source_by_symbol.get(symbol, "unknown"),
                            "status": "UNSUPPORTED",
                            "open_orders": "n/a",
                            "detail": "my-open-orders is unsupported for this symbol",
                        }
                    )
                else:
                    rows.append(
                        {
                            "symbol": symbol,
                            "source": market_source_by_symbol.get(symbol, "unknown"),
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

    market_probe_state_key = "account_market_open_order_support_probe"
    market_probe_status_filter_key = "account_market_open_order_support_status_filter"
    market_probe_detail_key = "account_market_open_order_support_detail_symbol"
    market_symbols = list(market_universe.get("symbols", []))

    with st.expander(
        "Full Market Open-Order Compatibility Probe",
        expanded=bool(st.session_state.get(market_probe_state_key)),
    ):
        st.caption(
            "Probe every currently listed Bitkub symbol against the known my-open-orders request variants so you can see which coins still return endpoint-not-found and which request recipe actually works."
        )
        st.caption(
            "Known attempts: quote_base_lower -> sym like btc_thb, base_quote_upper -> sym like BTC_THB, and without_symbol -> global open-orders."
        )

        probe_action_left, probe_action_right = st.columns(2)
        with probe_action_left:
            run_market_probe = st.button(
                "Probe All Market Symbols",
                key="account_market_open_order_support_probe_run",
                width='stretch',
            )
        with probe_action_right:
            clear_market_probe = st.button(
                "Clear Probe Results",
                key="account_market_open_order_support_probe_clear",
                width='stretch',
                disabled=not bool(st.session_state.get(market_probe_state_key)),
            )

        if clear_market_probe:
            st.session_state.pop(market_probe_state_key, None)

        if market_universe.get("error"):
            st.warning(f"Bitkub market symbols unavailable right now: {market_universe['error']}")
        elif market_symbols:
            st.caption(f"Loaded {len(market_symbols)} market symbols from Bitkub.")
        else:
            st.info("No Bitkub market symbols were returned right now.")

        if run_market_probe:
            if private_ctx.get("client") is None:
                st.error("Private API client is unavailable for the full market probe.")
            elif market_universe.get("error") or not market_symbols:
                st.warning("Bitkub market symbols are unavailable right now, so the full market probe cannot start.")
            else:
                with st.spinner(f"Probing open-order compatibility across {len(market_symbols)} symbols..."):
                    st.session_state[market_probe_state_key] = probe_open_orders_support_snapshot(
                        private_ctx["client"],
                        symbols=market_symbols,
                        source_by_symbol=market_source_by_symbol,
                    )

        market_probe = st.session_state.get(market_probe_state_key)
        if isinstance(market_probe, dict) and market_probe:
            summary = dict(market_probe.get("summary") or {})
            summary_cols = st.columns(5)
            with summary_cols[0]:
                render_metric_card("Market Symbols", str(int(summary.get("symbols", 0) or 0)), "probe scope")
            with summary_cols[1]:
                render_metric_card("Supported", str(int(summary.get("supported", 0) or 0)), "symbol-specific query works")
            with summary_cols[2]:
                render_metric_card("Global Only", str(int(summary.get("global_only", 0) or 0)), "filter global open orders")
            with summary_cols[3]:
                render_metric_card("Unsupported", str(int(summary.get("unsupported", 0) or 0)), "endpoint-not-found after known variants")
            with summary_cols[4]:
                render_metric_card("Other Errors", str(int(summary.get("error", 0) or 0)), "non-standard failures")

            unsupported_symbols = [str(symbol) for symbol in market_probe.get("unsupported_symbols", []) if str(symbol)]
            if unsupported_symbols:
                render_callout(
                    "Unsupported Symbols",
                    f"{len(unsupported_symbols)} symbol(s) still return endpoint-not-found after all known request variants. Keep these out of live open-order tracking for now.",
                    "warn",
                )
                st.code("\n".join(unsupported_symbols), language="text")

            status_options = ["ALL", "UNSUPPORTED", "ERROR", "GLOBAL_ONLY", "SUPPORTED"]
            selected_market_probe_status = st.selectbox(
                "Full Probe Status Filter",
                status_options,
                index=0,
                key=market_probe_status_filter_key,
            )
            market_probe_rows = list(market_probe.get("rows") or [])
            if selected_market_probe_status != "ALL":
                market_probe_rows = [
                    row
                    for row in market_probe_rows
                    if str(row.get("status") or "") == selected_market_probe_status
                ]

            if market_probe_rows:
                st.dataframe(market_probe_rows, width='stretch', hide_index=True)
            else:
                st.caption("No full-probe rows match the selected status filter.")

            detail_symbols = [
                str(row.get("symbol") or "")
                for row in list(market_probe.get("rows") or [])
                if str(row.get("symbol") or "")
            ]
            if detail_symbols:
                selected_detail_symbol = st.selectbox(
                    "Inspect Full Probe Details",
                    detail_symbols,
                    index=0,
                    key=market_probe_detail_key,
                )
                detail_payload = dict(market_probe.get("details_by_symbol", {}) or {}).get(
                    selected_detail_symbol,
                    {},
                )
                if detail_payload:
                    st.json(detail_payload, expanded=False)

            render_callout(
                "Next-Step Guide",
                "SUPPORTED means the row already tells you which request recipe works. GLOBAL_ONLY means use the global open-orders call and filter locally. UNSUPPORTED means both symbol-specific formats failed with endpoint-not-found, so there is no known sym-format fix right now and the coin should stay out of live open-order tracking.",
                "info",
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
    quote_fetched_at: str | None = None,
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

    manual_symbol_key = "live_ops_manual_symbol"
    manual_side_key = "live_ops_manual_side"
    manual_order_type_key = "live_ops_manual_order_type"
    manual_amount_thb_key = "live_ops_manual_amount_thb"
    manual_amount_coin_key = "live_ops_manual_amount_coin"
    manual_rate_key = "live_ops_manual_rate"
    manual_confirm_key = "live_ops_manual_confirm"

    pending_prefill = st.session_state.pop("live_ops_manual_order_prefill", None)
    if isinstance(pending_prefill, dict):
        prefill_symbol = str(pending_prefill.get("symbol", ""))
        if prefill_symbol in symbols:
            st.session_state[manual_symbol_key] = prefill_symbol
            prefill_side = str(pending_prefill.get("side", "sell")).lower()
            if prefill_side in {"buy", "sell"}:
                st.session_state[manual_side_key] = prefill_side
            prefill_order_type = str(pending_prefill.get("order_type", "limit")).lower()
            if prefill_order_type in {"limit"}:
                st.session_state[manual_order_type_key] = prefill_order_type
            if "amount_thb" in pending_prefill:
                st.session_state[manual_amount_thb_key] = _safe_float(
                    pending_prefill.get("amount_thb"),
                    _safe_float(manual_defaults.get("amount_thb", 100.0), 100.0),
                )
            if "amount_coin" in pending_prefill:
                st.session_state[manual_amount_coin_key] = _safe_float(
                    pending_prefill.get("amount_coin"),
                    _safe_float(manual_defaults.get("amount_coin", 0.0), 0.0),
                )
            if "rate" in pending_prefill:
                st.session_state[manual_rate_key] = _safe_float(
                    pending_prefill.get("rate"),
                    _safe_float(manual_defaults.get("rate", 1.0), 1.0),
                )
            st.session_state[manual_confirm_key] = False

    if st.session_state.get(manual_symbol_key) not in symbols:
        st.session_state[manual_symbol_key] = (
            default_symbol if default_symbol in symbols else symbols[0]
        )
    if st.session_state.get(manual_side_key) not in {"buy", "sell"}:
        st.session_state[manual_side_key] = str(manual_defaults.get("side", "buy"))
    if st.session_state.get(manual_order_type_key) not in {"limit"}:
        st.session_state[manual_order_type_key] = "limit"
    if manual_amount_thb_key not in st.session_state:
        st.session_state[manual_amount_thb_key] = _safe_float(
            manual_defaults.get("amount_thb", 100.0),
            100.0,
        )
    if manual_amount_coin_key not in st.session_state:
        st.session_state[manual_amount_coin_key] = _safe_float(
            manual_defaults.get("amount_coin", 0.0),
            0.0,
        )
    if manual_rate_key not in st.session_state:
        st.session_state[manual_rate_key] = _safe_float(
            latest_prices.get(st.session_state[manual_symbol_key], manual_defaults.get("rate", 1.0)),
            _safe_float(manual_defaults.get("rate", 1.0), 1.0),
        )
    if manual_confirm_key not in st.session_state:
        st.session_state[manual_confirm_key] = False

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
            "latest_auto_exit_slippage_block": _latest_auto_exit_slippage_block_row(),
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

        slippage_block_row = dynamic.get("latest_auto_exit_slippage_block")
        if slippage_block_row:
            details = dict(slippage_block_row.get("details") or {})
            candidate = dict(details.get("candidate") or {})
            symbol = str(
                slippage_block_row.get("symbol")
                or candidate.get("symbol")
                or ""
            )
            requested_sell_rate = _safe_float(
                slippage_block_row.get("request_rate"),
                _safe_float(candidate.get("rate"), 0.0),
            )
            tolerance_percent = _safe_float(
                dict(details.get("guardrails") or {}).get(
                    "live_slippage_tolerance_percent",
                ),
                _safe_float(config.get("live_slippage_tolerance_percent"), 0.0),
            )
            latest_live_price = _safe_float(latest_prices.get(symbol), 0.0)
            decision_time_price = _safe_float(
                slippage_block_row.get("latest_price"),
                _safe_float(candidate.get("latest_price"), 0.0),
            )
            amount_coin = _safe_float(
                slippage_block_row.get("amount_coin"),
                _safe_float(candidate.get("amount_coin"), 0.0),
            )
            resolution = build_exit_guardrail_resolution(
                symbol=symbol,
                requested_sell_rate=requested_sell_rate,
                latest_price=latest_live_price,
                live_slippage_tolerance_percent=tolerance_percent,
                quote_observed_at=str(quote_fetched_at or ""),
                quote_checked_at=now_text(),
            )

            render_section_intro(
                "Exit Guardrail Resolution Helper",
                "Latest slippage-blocked auto exit with one-time manual reprice options. No order is sent automatically.",
                "Auto Exit",
            )
            st.markdown(
                " ".join(
                    [
                        badge(f"symbol {symbol}", "info"),
                        badge(
                            f"blocked_at {slippage_block_row.get('created_at', 'n/a')}",
                            "warn",
                        ),
                        badge(
                            f"exit_reason {slippage_block_row.get('exit_reason', 'n/a')}",
                            "info",
                        ),
                    ]
                ),
                unsafe_allow_html=True,
            )

            latest_live_price_value = resolution.get("latest_live_price")
            deviation_percent = resolution.get("deviation_percent")
            band_low = resolution.get("allowed_sell_band_low")
            band_high = resolution.get("allowed_sell_band_high")
            suggested_rate = resolution.get("suggested_safe_sell_rate")
            quote_freshness = str(resolution.get("quote_freshness") or "unknown")
            quote_age_seconds = resolution.get("quote_age_seconds")

            st.caption(
                f"Latest live price: {latest_live_price_value:,.8f}"
                if latest_live_price_value is not None
                else "Latest live price: unavailable from current quote snapshot."
            )
            st.caption(f"Requested sell rate: {requested_sell_rate:,.8f}")
            st.caption(
                f"Deviation from latest live price: {deviation_percent:.2f}%"
                if deviation_percent is not None
                else "Deviation from latest live price: n/a"
            )
            st.caption(
                f"Configured live_slippage_tolerance_percent: {tolerance_percent:.2f}%"
            )
            st.caption(
                f"Allowed sell band from latest live price: {band_low:,.8f} to {band_high:,.8f}"
                if band_low is not None and band_high is not None
                else "Allowed sell band from latest live price: n/a"
            )
            st.caption(
                f"Suggested safe sell rate: {suggested_rate:,.8f}"
                if suggested_rate is not None
                else "Suggested safe sell rate: unavailable (quote stale or unavailable)."
            )
            if quote_age_seconds is not None:
                st.caption(
                    f"Quote freshness: {quote_freshness} ({quote_age_seconds:.0f}s old)"
                )
            else:
                st.caption(f"Quote freshness: {quote_freshness}")
            if decision_time_price > 0:
                st.caption(
                    f"Decision-time price at block event: {decision_time_price:,.8f}"
                )

            quote_safe = bool(resolution.get("quote_safe_for_suggestion"))
            if not quote_safe:
                st.warning(
                    "Quote is stale or unavailable. Safe one-time rate suggestions are disabled until a fresh quote is available."
                )

            st.caption(
                "One-time reprice only: these actions prefill the manual order form for this round and do not change saved rule values or tolerance."
            )
            action_left, action_right, action_tune, action_compare = st.columns(4)
            with action_left:
                use_latest = st.button(
                    "Use Latest Price (One-time)",
                    disabled=(not quote_safe) or latest_live_price_value is None or amount_coin <= 0,
                    key=f"exit_guardrail_use_latest_{symbol}",
                    width='stretch',
                )
            with action_right:
                use_safe_edge = st.button(
                    "Use Safe Edge (One-time)",
                    disabled=(not quote_safe) or suggested_rate is None or amount_coin <= 0,
                    key=f"exit_guardrail_use_safe_{symbol}",
                    width='stretch',
                )
            with action_tune:
                open_tuning = st.button(
                    "Open Live Tuning",
                    disabled=not bool(symbol),
                    key=f"exit_guardrail_open_tuning_{symbol}",
                    width='stretch',
                )
            with action_compare:
                open_compare = st.button(
                    "Open Compare",
                    disabled=not bool(symbol),
                    key=f"exit_guardrail_open_compare_{symbol}",
                    width='stretch',
                )

            if use_latest and latest_live_price_value is not None:
                _queue_manual_one_time_sell_prefill(
                    symbol=symbol,
                    amount_coin=amount_coin,
                    rate=float(latest_live_price_value),
                )
                _set_live_ops_feedback(
                    "Manual form prefilled from exit helper",
                    [
                        f"symbol={symbol} side=sell amount_coin={amount_coin:,.8f}",
                        f"rate={float(latest_live_price_value):,.8f} (latest live price)",
                        "Review and submit manually to execute once.",
                    ],
                    tone="warning",
                )
                st.rerun()

            if use_safe_edge and suggested_rate is not None:
                _queue_manual_one_time_sell_prefill(
                    symbol=symbol,
                    amount_coin=amount_coin,
                    rate=float(suggested_rate),
                )
                _set_live_ops_feedback(
                    "Manual form prefilled from exit helper",
                    [
                        f"symbol={symbol} side=sell amount_coin={amount_coin:,.8f}",
                        f"rate={float(suggested_rate):,.8f} (safe edge in slippage band)",
                        "Review and submit manually to execute once.",
                    ],
                    tone="warning",
                )
                st.rerun()

            if open_tuning:
                _open_strategy_workspace_for_symbol(
                    symbol=symbol,
                    workspace="Live Tuning",
                )
            if open_compare:
                _open_strategy_workspace_for_symbol(
                    symbol=symbol,
                    workspace="Compare",
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
            current_manual_symbol = str(st.session_state.get(manual_symbol_key, default_symbol))
            if current_manual_symbol not in symbols:
                current_manual_symbol = default_symbol if default_symbol in symbols else symbols[0]
                st.session_state[manual_symbol_key] = current_manual_symbol
            symbol = st.selectbox(
                "Symbol",
                symbols,
                index=max(0, symbols.index(current_manual_symbol)),
                key=manual_symbol_key,
            )
            side = st.selectbox(
                "Side",
                ["buy", "sell"],
                index=0 if str(st.session_state.get(manual_side_key, "buy")) == "buy" else 1,
                key=manual_side_key,
            )
            order_type = st.selectbox(
                "Order Type",
                ["limit"],
                index=0,
                key=manual_order_type_key,
            )
            amount_thb = st.number_input(
                "Amount THB",
                min_value=0.0,
                step=10.0,
                key=manual_amount_thb_key,
            )
            amount_coin = st.number_input(
                "Amount Coin",
                min_value=0.0,
                format="%.8f",
                key=manual_amount_coin_key,
            )
            rate = st.number_input(
                "Rate",
                min_value=0.0,
                format="%.8f",
                key=manual_rate_key,
            )
            confirm = st.checkbox(
                "I understand this can submit a real Bitkub order.",
                key=manual_confirm_key,
            )
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
