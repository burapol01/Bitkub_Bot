from __future__ import annotations

from typing import Any

import streamlit as st

from services.db_service import fetch_open_execution_orders, insert_runtime_event
from services.execution_service import build_live_price_band_resolution
from ui.streamlit.config_support import save_config_with_feedback
from ui.streamlit.navigation import (
    queue_live_ops_navigation,
    queue_strategy_workspace_navigation,
)
from ui.streamlit.styles import badge, render_callout, render_metric_card, render_section_intro
from ui.streamlit.symbol_state import build_symbol_operational_state
from utils.time_utils import now_text


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _sync_select_state(*, key: str, options: list[str], default: str) -> None:
    normalized_options = [str(option) for option in options]
    if not normalized_options:
        st.session_state.pop(key, None)
        st.session_state.pop(f"{key}__signature", None)
        return

    normalized_default = (
        str(default)
        if str(default) in normalized_options
        else normalized_options[0]
    )
    signature = tuple(normalized_options)
    signature_key = f"{key}__signature"
    current_value = st.session_state.get(key)

    if st.session_state.get(signature_key) != signature:
        st.session_state[key] = normalized_default
        st.session_state[signature_key] = signature
        return

    if str(current_value) not in normalized_options:
        st.session_state[key] = normalized_default


def _default_symbol_from_context(configured_symbols: list[str]) -> str:
    preferred_keys = [
        "execution_assistant_symbol_autorun",
        "live_ops_focus_symbol",
        "strategy_tuning_focus_symbol",
        "strategy_compare_symbol",
        "execution_assistant_symbol",
    ]
    for key in preferred_keys:
        candidate = str(st.session_state.pop(key, "") if key == "execution_assistant_symbol_autorun" else st.session_state.get(key, "")).strip()
        if candidate in configured_symbols:
            return candidate
    return configured_symbols[0]


def _sync_rule_draft_state(*, symbol: str, buy_below: float, sell_above: float) -> None:
    draft_signature = (
        str(symbol),
        round(float(buy_below), 8),
        round(float(sell_above), 8),
    )
    signature_key = "execution_assistant_rule_draft_signature"
    if st.session_state.get(signature_key) != draft_signature:
        st.session_state["execution_assistant_draft_buy_below"] = float(buy_below)
        st.session_state["execution_assistant_draft_sell_above"] = float(sell_above)
        st.session_state[signature_key] = draft_signature


def _consume_draft_autorun() -> None:
    draft_autorun = dict(st.session_state.pop("execution_assistant_draft_autorun", {}) or {})
    if "buy_below" in draft_autorun:
        st.session_state["execution_assistant_draft_buy_below"] = _safe_float(
            draft_autorun.get("buy_below"),
            _safe_float(st.session_state.get("execution_assistant_draft_buy_below")),
        )
    if "sell_above" in draft_autorun:
        st.session_state["execution_assistant_draft_sell_above"] = _safe_float(
            draft_autorun.get("sell_above"),
            _safe_float(st.session_state.get("execution_assistant_draft_sell_above")),
        )


def _format_quote_freshness(resolution: dict[str, Any]) -> str:
    freshness = str(resolution.get("quote_freshness") or "unknown")
    quote_age_seconds = resolution.get("quote_age_seconds")
    if quote_age_seconds is None:
        return freshness
    return f"{freshness} ({float(quote_age_seconds):.0f}s old)"


def _gap_percent(*, rate: float, latest_price: float) -> float | None:
    if float(latest_price) <= 0:
        return None
    return ((float(rate) - float(latest_price)) / float(latest_price)) * 100.0


def _resolution_status_label(resolution: dict[str, Any]) -> str:
    reason = str(resolution.get("suggestion_reason") or "no_quote")
    if reason == "already_inside_band":
        return "inside band"
    if reason in {"clamped_to_lower_band", "clamped_to_upper_band"}:
        return "outside band"
    if reason == "quote_stale":
        return "quote stale"
    return "quote unavailable"


def _open_compare_for_symbol(*, symbol: str) -> None:
    queue_strategy_workspace_navigation(workspace="Compare", symbol=symbol)
    st.query_params["page"] = "Strategy"
    st.rerun()


def _open_tuning_for_symbol(*, symbol: str) -> None:
    queue_strategy_workspace_navigation(workspace="Live Tuning", symbol=symbol)
    st.query_params["page"] = "Strategy"
    st.rerun()


def _open_live_ops_for_symbol(*, symbol: str) -> None:
    queue_live_ops_navigation(symbol=symbol)
    st.query_params["page"] = "Live Ops"
    st.rerun()


def render_execution_assistant_page(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any],
    runtime: dict[str, Any],
    latest_prices: dict[str, float],
    quote_fetched_at: str | None = None,
) -> None:
    render_section_intro(
        "Execution Assistant",
        "Review live quote distance against the current rule before you tighten entry or exit prices. Draft changes stay local until you save them explicitly.",
        "Execution",
    )
    render_callout(
        "Safety Rules",
        "No orders are submitted here. Snap actions only adjust the draft rule values, and live_slippage_tolerance_percent stays read-only on this page.",
        "info",
    )

    configured_symbols = sorted(str(symbol) for symbol in config.get("rules", {}).keys())
    if not configured_symbols:
        st.caption("No live rules are configured yet.")
        return

    default_symbol = _default_symbol_from_context(configured_symbols)
    _sync_select_state(
        key="execution_assistant_symbol",
        options=configured_symbols,
        default=default_symbol,
    )
    symbol = str(
        st.selectbox(
            "Symbol",
            configured_symbols,
            key="execution_assistant_symbol",
        )
    )

    current_rule = dict(config.get("rules", {}).get(symbol) or {})
    if not current_rule:
        st.warning(f"No live rule was found for {symbol}.")
        return

    buy_below = _safe_float(current_rule.get("buy_below"))
    sell_above = _safe_float(current_rule.get("sell_above"))
    tolerance_percent = _safe_float(config.get("live_slippage_tolerance_percent"))
    latest_price = _safe_float(latest_prices.get(symbol))
    checked_at = now_text()
    open_execution_orders = fetch_open_execution_orders()
    state = build_symbol_operational_state(
        symbol=symbol,
        config=config,
        account_snapshot=private_ctx.get("account_snapshot"),
        latest_prices=latest_prices,
        runtime=runtime,
        execution_orders=open_execution_orders,
    )

    buy_resolution = build_live_price_band_resolution(
        symbol=symbol,
        side="buy",
        requested_rate=buy_below,
        latest_price=latest_price,
        live_slippage_tolerance_percent=tolerance_percent,
        quote_observed_at=quote_fetched_at,
        quote_checked_at=checked_at,
    )
    sell_resolution = build_live_price_band_resolution(
        symbol=symbol,
        side="sell",
        requested_rate=sell_above,
        latest_price=latest_price,
        live_slippage_tolerance_percent=tolerance_percent,
        quote_observed_at=quote_fetched_at,
        quote_checked_at=checked_at,
    )

    _sync_rule_draft_state(
        symbol=symbol,
        buy_below=buy_below,
        sell_above=sell_above,
    )
    _consume_draft_autorun()

    draft_buy_below = float(
        st.number_input(
            "Draft Buy Below",
            min_value=0.0,
            format="%.8f",
            key="execution_assistant_draft_buy_below",
        )
    )
    draft_sell_above = float(
        st.number_input(
            "Draft Sell Above",
            min_value=0.0,
            format="%.8f",
            key="execution_assistant_draft_sell_above",
        )
    )

    buy_gap_percent = _gap_percent(rate=buy_below, latest_price=latest_price)
    sell_gap_percent = _gap_percent(rate=sell_above, latest_price=latest_price)
    draft_buy_gap_percent = _gap_percent(rate=draft_buy_below, latest_price=latest_price)
    draft_sell_gap_percent = _gap_percent(rate=draft_sell_above, latest_price=latest_price)
    allowed_band_low = buy_resolution.get("allowed_band_low")
    allowed_band_high = buy_resolution.get("allowed_band_high")
    quote_safe = bool(
        buy_resolution.get("quote_safe_for_suggestion")
        and sell_resolution.get("quote_safe_for_suggestion")
    )

    status_badges = [
        badge(f"symbol {symbol}", "info"),
        badge(
            f"quote {_format_quote_freshness(buy_resolution)}",
            "good" if quote_safe else "warn",
        ),
        badge(
            f"open buy {int(state.get('open_buy_count', 0))}",
            "warn" if int(state.get("open_buy_count", 0)) else "good",
        ),
        badge(
            f"open sell {int(state.get('open_sell_count', 0))}",
            "warn" if int(state.get("open_sell_count", 0)) else "good",
        ),
        badge(
            f"reserved THB {float(state.get('reserved_thb', 0.0)):,.2f}",
            "warn" if float(state.get("reserved_thb", 0.0)) > 0 else "good",
        ),
        badge(
            f"reserved coin {float(state.get('reserved_coin', 0.0)):,.8f}",
            "warn" if float(state.get("reserved_coin", 0.0)) > 0 else "good",
        ),
    ]
    st.markdown(" ".join(status_badges), unsafe_allow_html=True)

    metric_cols = st.columns(4)
    with metric_cols[0]:
        render_metric_card(
            "Latest Price",
            f"{latest_price:,.8f}" if latest_price > 0 else "n/a",
            symbol,
        )
    with metric_cols[1]:
        render_metric_card(
            "Buy Gap",
            f"{buy_gap_percent:+.2f}%" if buy_gap_percent is not None else "n/a",
            f"buy_below {buy_below:,.8f}",
        )
    with metric_cols[2]:
        render_metric_card(
            "Sell Gap",
            f"{sell_gap_percent:+.2f}%" if sell_gap_percent is not None else "n/a",
            f"sell_above {sell_above:,.8f}",
        )
    with metric_cols[3]:
        render_metric_card(
            "Slippage Tolerance",
            f"{tolerance_percent:.2f}%",
            (
                f"band {float(allowed_band_low):,.8f} to {float(allowed_band_high):,.8f}"
                if allowed_band_low is not None and allowed_band_high is not None
                else "band unavailable"
            ),
        )

    render_callout(
        "Operational State",
        f"{state['state_summary']}<br>{state['risk_summary']}",
        "info",
    )
    if state.get("entry_block_reasons"):
        st.caption("Entry blocked: " + "; ".join(str(reason) for reason in state["entry_block_reasons"]))
    if state.get("exit_block_reasons"):
        st.caption("Exit blocked: " + "; ".join(str(reason) for reason in state["exit_block_reasons"]))
    if state.get("review_required"):
        st.warning("Review required: " + "; ".join(str(reason) for reason in state.get("review_reasons", [])))

    recent_guardrail_block = dict(state.get("recent_guardrail_block") or {})
    recent_message = str(recent_guardrail_block.get("message") or "").strip()
    if recent_message:
        render_callout(
            "Recent Guardrail Block",
            (
                f"{recent_message}<br>"
                f"channel={recent_guardrail_block.get('channel', 'n/a')} | "
                f"at={recent_guardrail_block.get('created_at', 'n/a')}"
            ),
            "warn",
        )

    pricing_rows = [
        {
            "target": "buy_below",
            "current_rule": f"{buy_below:,.8f}",
            "current_gap": f"{buy_gap_percent:+.2f}%" if buy_gap_percent is not None else "n/a",
            "draft_rule": f"{draft_buy_below:,.8f}",
            "draft_gap": f"{draft_buy_gap_percent:+.2f}%" if draft_buy_gap_percent is not None else "n/a",
            "allowed_band": (
                f"{float(allowed_band_low):,.8f} to {float(allowed_band_high):,.8f}"
                if allowed_band_low is not None and allowed_band_high is not None
                else "n/a"
            ),
            "suggested_safe": (
                f"{float(buy_resolution['suggested_safe_rate']):,.8f}"
                if buy_resolution.get("suggested_safe_rate") is not None
                else "n/a"
            ),
            "guardrail_status": _resolution_status_label(buy_resolution),
        },
        {
            "target": "sell_above",
            "current_rule": f"{sell_above:,.8f}",
            "current_gap": f"{sell_gap_percent:+.2f}%" if sell_gap_percent is not None else "n/a",
            "draft_rule": f"{draft_sell_above:,.8f}",
            "draft_gap": f"{draft_sell_gap_percent:+.2f}%" if draft_sell_gap_percent is not None else "n/a",
            "allowed_band": (
                f"{float(allowed_band_low):,.8f} to {float(allowed_band_high):,.8f}"
                if allowed_band_low is not None and allowed_band_high is not None
                else "n/a"
            ),
            "suggested_safe": (
                f"{float(sell_resolution['suggested_safe_rate']):,.8f}"
                if sell_resolution.get("suggested_safe_rate") is not None
                else "n/a"
            ),
            "guardrail_status": _resolution_status_label(sell_resolution),
        },
    ]
    st.dataframe(pricing_rows, width="stretch", hide_index=True)

    if not quote_safe:
        st.warning(
            "Quote is stale or unavailable. Snap actions and rule save stay disabled until a fresh quote is available."
        )

    draft_changed = (
        round(draft_buy_below, 8) != round(buy_below, 8)
        or round(draft_sell_above, 8) != round(sell_above, 8)
    )
    invalid_rule = draft_buy_below >= draft_sell_above
    if invalid_rule:
        st.error("Draft buy_below must stay below draft sell_above before the rule can be saved.")
    elif draft_changed:
        st.caption(
            "Pending rule update: "
            f"buy_below {buy_below:,.8f} -> {draft_buy_below:,.8f} | "
            f"sell_above {sell_above:,.8f} -> {draft_sell_above:,.8f}"
        )
    else:
        st.caption("Draft matches the current saved live rule.")

    snap_cols = st.columns(3)
    with snap_cols[0]:
        if st.button(
            "Snap Buy To Safe Band",
            key="execution_assistant_snap_buy",
            disabled=(not quote_safe) or buy_resolution.get("suggested_safe_rate") is None,
            width="stretch",
        ):
            st.session_state["execution_assistant_draft_autorun"] = {
                "buy_below": float(buy_resolution["suggested_safe_rate"]),
            }
            st.rerun()
    with snap_cols[1]:
        if st.button(
            "Snap Sell To Safe Band",
            key="execution_assistant_snap_sell",
            disabled=(not quote_safe) or sell_resolution.get("suggested_safe_rate") is None,
            width="stretch",
        ):
            st.session_state["execution_assistant_draft_autorun"] = {
                "sell_above": float(sell_resolution["suggested_safe_rate"]),
            }
            st.rerun()
    with snap_cols[2]:
        if st.button(
            "Snap Both",
            key="execution_assistant_snap_both",
            disabled=(
                (not quote_safe)
                or buy_resolution.get("suggested_safe_rate") is None
                or sell_resolution.get("suggested_safe_rate") is None
            ),
            width="stretch",
        ):
            st.session_state["execution_assistant_draft_autorun"] = {
                "buy_below": float(buy_resolution["suggested_safe_rate"]),
                "sell_above": float(sell_resolution["suggested_safe_rate"]),
            }
            st.rerun()

    save_disabled = (not quote_safe) or (not draft_changed) or invalid_rule
    if st.button(
        "Save Adjusted Rule",
        key="execution_assistant_save_rule",
        disabled=save_disabled,
        type="primary",
        width="stretch",
    ):
        updated_rule = dict(current_rule)
        updated_rule["buy_below"] = float(draft_buy_below)
        updated_rule["sell_above"] = float(draft_sell_above)

        updated = dict(config)
        updated_rules = dict(config.get("rules") or {})
        updated_rules[symbol] = updated_rule
        updated["rules"] = updated_rules

        if save_config_with_feedback(
            config,
            updated,
            f"Saved execution assistant pricing for {symbol}",
            audit_action_type="execution_assistant_rule_adjustment",
            audit_reason="manual live rule price adjustment from execution assistant",
        ):
            insert_runtime_event(
                created_at=now_text(),
                event_type="execution_assistant",
                severity="info",
                message=f"Adjusted live rule pricing for {symbol}",
                details={
                    "symbol": symbol,
                    "buy_below": float(draft_buy_below),
                    "sell_above": float(draft_sell_above),
                    "latest_price": float(latest_price),
                    "live_slippage_tolerance_percent": float(tolerance_percent),
                },
            )
            st.session_state["execution_assistant_rule_draft_signature"] = (
                symbol,
                round(float(draft_buy_below), 8),
                round(float(draft_sell_above), 8),
            )
            st.rerun()

    nav_cols = st.columns(3)
    with nav_cols[0]:
        if st.button(
            "Open Compare",
            key="execution_assistant_open_compare",
            width="stretch",
        ):
            _open_compare_for_symbol(symbol=symbol)
    with nav_cols[1]:
        if st.button(
            "Open Live Tuning",
            key="execution_assistant_open_tuning",
            width="stretch",
        ):
            _open_tuning_for_symbol(symbol=symbol)
    with nav_cols[2]:
        if st.button(
            "Open Live Ops",
            key="execution_assistant_open_live_ops",
            width="stretch",
        ):
            _open_live_ops_for_symbol(symbol=symbol)
