from __future__ import annotations

from typing import Any

import streamlit as st

from services.account_service import build_live_holdings_snapshot
from services.db_service import (
    fetch_dashboard_summary,
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
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
from ui.streamlit.data import calc_daily_totals
from ui.streamlit.refresh import render_refreshable_fragment
from ui.streamlit.styles import badge, render_metric_card
from utils.time_utils import now_text


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
        width='stretch',
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
            '<div class="note-strip"><strong>Runtime Restore</strong><br>' + "<br>".join(runtime["messages"]) + '</div>',
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
            st.dataframe(holdings, width='stretch', hide_index=True)
        else:
            st.caption("No live holdings found in the current account snapshot.")
    with right:
        st.markdown('<div class="panel-title">Exchange Open Orders</div>', unsafe_allow_html=True)
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
            st.markdown('<div class="panel-title">Recent Execution Orders</div>', unsafe_allow_html=True)
            st.dataframe(recent_execution_orders, width='stretch', hide_index=True)
        with history_right:
            st.markdown('<div class="panel-title">Recent Execution Events</div>', unsafe_allow_html=True)
            st.dataframe(recent_execution_events, width='stretch', hide_index=True)

    render_refreshable_fragment(auto_refresh_run_every, _render_live_ops_history)
