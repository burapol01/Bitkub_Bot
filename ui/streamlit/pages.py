from __future__ import annotations

from collections import defaultdict
from typing import Any, MutableMapping

import streamlit as st

from clients.bitkub_private_client import BitkubMissingCredentialsError, BitkubPrivateClient
from config import CONFIG_PATH, ordered_unique_symbols

from services.db_service import (
    DB_PATH,
    fetch_latest_market_candle_timestamp,
    fetch_latest_market_snapshot_timestamp,
    fetch_open_execution_orders,
    fetch_reports_page_dataset,
    fetch_runtime_event_log,
    insert_runtime_event,
)
from services.execution_service import cancel_live_order, refresh_live_order_from_exchange
from services.strategy_lab_service import (
    build_coin_ranking,
    fetch_market_snapshot_coverage,
    fetch_trade_analytics,
    run_market_candle_replay,
    run_market_snapshot_replay,
    sync_candles_for_symbols,
)
from ui.streamlit.actions import persist_execution_order_update
from ui.streamlit.config_support import render_config_page, save_config_with_feedback
from ui.streamlit.navigation import queue_live_ops_navigation, queue_strategy_workspace_navigation
from ui.streamlit.ops_pages import render_account_page, render_live_ops_page, render_overview_page
from ui.streamlit.diagnostics_support import render_diagnostics_page, render_logs_page
from ui.streamlit.data import calc_daily_totals, capability_badge_tone
from ui.streamlit.refresh import PAGE_ORDER, render_refreshable_fragment
from ui.streamlit.styles import badge, render_callout, render_metric_card, render_section_intro, render_sidebar_block
from ui.streamlit.symbol_state import build_symbol_operational_state
from ui.streamlit.strategy_support import (
    annotate_strategy_compare_rows,
    build_live_rule_tuning_rows,
    build_rule_compare_variants,
    build_rule_seed,
    evaluate_fee_guardrail,
    fetch_market_symbol_universe,
    run_strategy_compare_rows,
)
from utils.time_utils import now_dt, now_text, parse_time_text


def _summarize_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, int] = defaultdict(int)
    for line in lines:
        grouped[str(line)] += 1
    return [
        {"message": message, "count": count}
        for message, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    ]


def _sync_multiselect_state(*, key: str, options: list[str], default: list[str]) -> None:
    normalized_options = [str(option) for option in options]
    normalized_default = [
        str(value) for value in default if str(value) in normalized_options
    ]
    signature = (tuple(normalized_options), tuple(normalized_default))
    signature_key = f"{key}__signature"
    current_value = st.session_state.get(key)

    if st.session_state.get(signature_key) != signature:
        st.session_state[key] = list(normalized_default)
        st.session_state[signature_key] = signature
        return

    if isinstance(current_value, list):
        filtered_value = [
            str(value) for value in current_value if str(value) in normalized_options
        ]
        if filtered_value != list(current_value):
            st.session_state[key] = filtered_value


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


def _clear_strategy_compare_state() -> None:
    for key in (
        "strategy_compare_payload",
        "strategy_compare_symbol",
        "strategy_compare_symbol__input",
        "strategy_compare_symbol__input__signature",
        "strategy_compare_source",
        "strategy_compare_source__input",
        "strategy_compare_source__input__signature",
        "strategy_compare_resolution",
        "strategy_compare_resolution__input",
        "strategy_compare_resolution__input__signature",
        "strategy_compare_days",
        "strategy_compare_days__input",
    ):
        st.session_state.pop(key, None)


def _blocked_execution_order_symbols(
    *,
    symbols: list[str],
    open_orders: list[dict[str, Any]],
) -> list[str]:
    target_symbols = {
        str(symbol).strip()
        for symbol in symbols
        if str(symbol).strip()
    }
    return sorted(
        {
            str(order.get("symbol", "")).strip()
            for order in open_orders
            if str(order.get("symbol", "")).strip() in target_symbols
        }
    )


def _refresh_open_execution_orders_for_ui() -> tuple[list[dict[str, Any]], list[str]]:
    credential_warning = (
        "Private API credentials are not configured, so prune revalidation used local execution-order state only."
    )
    try:
        client = BitkubPrivateClient.from_env()
    except BitkubMissingCredentialsError:
        return fetch_open_execution_orders(), [credential_warning]

    if not client.is_configured():
        return fetch_open_execution_orders(), [credential_warning]

    refresh_errors: list[str] = []
    open_orders = fetch_open_execution_orders()
    for order in open_orders:
        try:
            refreshed_record, refresh_events = refresh_live_order_from_exchange(
                client=client,
                order_record=order,
                occurred_at=now_text(),
            )
            persist_execution_order_update(
                int(order["id"]),
                refreshed_record,
                refresh_events,
            )
        except Exception as e:
            refresh_errors.append(
                f"{order['symbol']}: unable to refresh exchange state ({e})"
            )

    return fetch_open_execution_orders(), refresh_errors


def _revalidate_prune_blocked_symbols(
    *,
    symbols_to_prune: list[str],
) -> tuple[list[str], list[str]]:
    initial_blocked = _blocked_execution_order_symbols(
        symbols=symbols_to_prune,
        open_orders=fetch_open_execution_orders(),
    )
    if not initial_blocked:
        return [], []

    refreshed_open_orders, refresh_errors = _refresh_open_execution_orders_for_ui()
    blocked_symbols = _blocked_execution_order_symbols(
        symbols=symbols_to_prune,
        open_orders=refreshed_open_orders,
    )
    return blocked_symbols, refresh_errors


def _open_live_ops_for_symbol(*, symbol: str) -> None:
    queue_live_ops_navigation(symbol=symbol)
    st.rerun()


def _queue_strategy_workspace(*, workspace: str, symbol: str | None = None) -> None:
    queue_strategy_workspace_navigation(workspace=workspace, symbol=symbol)


def _render_symbol_operational_state(
    *,
    symbol: str,
    config: dict[str, Any],
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
    runtime: dict[str, Any] | None = None,
    open_execution_orders: list[dict[str, Any]] | None = None,
    title: str,
    kicker: str,
) -> dict[str, Any]:
    state = build_symbol_operational_state(
        symbol=symbol,
        config=config,
        account_snapshot=private_ctx.get("account_snapshot"),
        latest_prices=latest_prices,
        runtime=runtime,
        execution_orders=open_execution_orders,
    )

    tone = "bad" if state["review_required"] else "warn" if state["entry_blocked"] or state["exit_blocked"] else "good"
    st.markdown(
        " ".join(
            [
                badge(f"symbol {state['symbol']}", "info"),
                badge(
                    f"open buy {state['open_buy_count']}",
                    "warn" if state["open_buy_count"] else "good",
                ),
                badge(
                    f"open sell {state['open_sell_count']}",
                    "warn" if state["open_sell_count"] else "good",
                ),
                badge(
                    f"reserved THB {state['reserved_thb']:,.2f}",
                    "warn" if state["reserved_thb"] > 0 else "good",
                ),
                badge(
                    f"reserved coin {state['reserved_coin']:,.8f}",
                    "warn" if state["reserved_coin"] > 0 else "good",
                ),
                badge(
                    "partial fill" if state["partial_fill"] else "no partial fill",
                    "warn" if state["partial_fill"] else "good",
                ),
                badge(
                    "review required" if state["review_required"] else "state clear",
                    tone,
                ),
            ]
        ),
        unsafe_allow_html=True,
    )
    render_callout(
        title,
        f"{state['state_summary']}<br>{state['risk_summary']}",
        "info",
    )
    if state["entry_block_reasons"]:
        st.caption("Entry blocked: " + "; ".join(state["entry_block_reasons"]))
    else:
        st.caption("Entry blocked: no symbol-level blockers were detected.")
    if state["exit_block_reasons"]:
        st.caption("Exit blocked: " + "; ".join(state["exit_block_reasons"]))
    else:
        st.caption("Exit blocked: no symbol-level blockers were detected.")
    if state["review_required"]:
        st.warning("Review required: " + "; ".join(state["review_reasons"]))
    recent_guardrail_block = state.get("recent_guardrail_block")
    if recent_guardrail_block:
        message = str(recent_guardrail_block.get("message") or "")
        if message:
            st.caption(
                "Recent guardrail block: "
                f"{message} ({recent_guardrail_block.get('channel', 'n/a')})"
            )
    return state


def _strategy_compare_scope_key(
    *,
    symbol: str,
    source: str,
    resolution: str,
    days: int,
) -> str:
    return "|".join(
        [
            str(symbol or "").strip(),
            str(source or "").strip(),
            str(resolution or "").strip(),
            str(int(days)),
        ]
    )


def _strategy_compare_candle_revision_key(*, symbol: str, resolution: str) -> str:
    return f"strategy_compare_candle_revision::{str(symbol).strip()}::{str(resolution).strip()}"


def _strategy_compare_cache_token(
    *,
    session_state: MutableMapping[str, Any] | None,
    symbol: str,
    source: str,
    resolution: str,
) -> str:
    if str(source) != "candles":
        return ""
    state = session_state if session_state is not None else st.session_state
    return str(
        state.get(
            _strategy_compare_candle_revision_key(
                symbol=str(symbol),
                resolution=str(resolution),
            ),
            "",
        )
        or ""
    )


def _invalidate_strategy_compare_state_for_candle_sync(
    *,
    sync_result: dict[str, Any],
    session_state: MutableMapping[str, Any] | None = None,
    revision_value: str | None = None,
) -> list[str]:
    state = session_state if session_state is not None else st.session_state
    synced_rows = [
        dict(row)
        for row in list(sync_result.get("synced") or [])
        if isinstance(row, dict)
    ]
    resolution = str(sync_result.get("resolution") or "").strip()
    if not synced_rows or not resolution:
        return []

    invalidated_scopes: list[str] = []
    revision = str(revision_value or now_text())
    current_payload = state.get("strategy_compare_payload")
    payload_matches = isinstance(current_payload, dict)

    for row in synced_rows:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        state[
            _strategy_compare_candle_revision_key(
                symbol=symbol,
                resolution=resolution,
            )
        ] = revision
        if (
            payload_matches
            and str(current_payload.get("source") or "") == "candles"
            and str(current_payload.get("symbol") or "") == symbol
            and str(current_payload.get("resolution") or "") == resolution
        ):
            invalidated_scopes.append(
                _strategy_compare_scope_key(
                    symbol=symbol,
                    source="candles",
                    resolution=resolution,
                    days=int(current_payload.get("days", 0) or 0),
                )
            )

    if invalidated_scopes:
        state.pop("strategy_compare_payload", None)

    return invalidated_scopes


def _compare_source_label(source: str) -> str:
    return "Candles" if str(source) == "candles" else "Snapshots"


def _compare_timestamp_label(source: str) -> str:
    return "Last Candle Timestamp" if str(source) == "candles" else "Last Snapshot Timestamp"


def _compare_resolution_seconds(resolution: str) -> int:
    resolution_key = str(resolution or "").strip().upper()
    mapping = {
        "1": 60,
        "5": 300,
        "15": 900,
        "60": 3600,
        "240": 14400,
        "1D": 86400,
    }
    return int(mapping.get(resolution_key, 0) or 0)


def _load_compare_data_last_timestamp(
    *,
    symbol: str,
    source: str,
    resolution: str,
) -> str | None:
    if str(source) == "candles":
        return fetch_latest_market_candle_timestamp(
            symbol=str(symbol),
            resolution=str(resolution),
        )
    if str(source) == "snapshots":
        return fetch_latest_market_snapshot_timestamp(symbol=str(symbol))
    return None


def _build_compare_data_freshness(
    *,
    source: str,
    resolution: str,
    last_timestamp: str | None,
    checked_at: Any | None = None,
) -> dict[str, Any]:
    source_label = _compare_source_label(str(source))
    timestamp_label = _compare_timestamp_label(str(source))
    normalized_timestamp = str(last_timestamp or "").strip()
    payload: dict[str, Any] = {
        "source": str(source),
        "source_label": source_label,
        "timestamp_label": timestamp_label,
        "last_timestamp": normalized_timestamp or None,
        "last_timestamp_text": normalized_timestamp or "Missing",
        "status": "Missing",
        "age_seconds": None,
        "tone": "bad",
        "warning": (
            f"No stored {source_label.lower()} data is available for this compare selection. "
            "Sync fresh data before running Compare or applying a variant."
        ),
    }
    if not normalized_timestamp or normalized_timestamp.lower() == "n/a":
        return payload

    try:
        checked_dt = checked_at if checked_at is not None else now_dt()
        observed_dt = parse_time_text(normalized_timestamp)
        age_seconds = max(0.0, (checked_dt - observed_dt).total_seconds())
    except Exception:
        return payload

    payload["age_seconds"] = age_seconds
    if str(source) == "candles":
        resolution_seconds = max(60, _compare_resolution_seconds(str(resolution)))
        fresh_cutoff = resolution_seconds * 2
        aging_cutoff = resolution_seconds * 6
    else:
        fresh_cutoff = 6 * 3600
        aging_cutoff = 24 * 3600

    if age_seconds <= fresh_cutoff:
        payload["status"] = "Fresh"
        payload["tone"] = "good"
        payload["warning"] = None
    elif age_seconds <= aging_cutoff:
        payload["status"] = "Aging"
        payload["tone"] = "warn"
        payload["warning"] = None
    else:
        payload["status"] = "Stale"
        payload["tone"] = "bad"
        payload["warning"] = (
            f"{source_label} data is stale for this compare selection. "
            "Sync fresh data before running Compare or applying a variant."
        )
    return payload


def _compare_payload_last_timestamp(rows: list[dict[str, Any]]) -> str | None:
    timestamps = [
        str(row.get("coverage_last_seen") or "").strip()
        for row in rows
        if str(row.get("coverage_last_seen") or "").strip()
        and str(row.get("coverage_last_seen") or "").strip().lower() != "n/a"
    ]
    if not timestamps:
        return None
    return max(timestamps)


def _render_compare_data_freshness(
    *,
    freshness: dict[str, Any],
) -> None:
    badges = [
        badge(f"source {freshness.get('source_label', 'n/a')}", "info"),
        badge(
            f"{str(freshness.get('timestamp_label') or 'Last Timestamp').lower()} {freshness.get('last_timestamp_text', 'Missing')}",
            "info",
        ),
        badge(f"freshness {freshness.get('status', 'Missing')}", str(freshness.get("tone") or "info")),
    ]
    st.markdown(" ".join(badges), unsafe_allow_html=True)
    warning = str(freshness.get("warning") or "").strip()
    if warning:
        st.warning(warning)


def _strategy_decision_action_rank(action: str) -> int:
    return {
        "Sync first": 0,
        "Promote": 1,
        "Prune candidate": 2,
        "Keep": 3,
    }.get(str(action), 9)


def _strategy_decision_freshness_rank(status: str) -> int:
    return {
        "Fresh": 0,
        "Aging": 1,
        "Stale": 2,
        "Missing": 3,
    }.get(str(status), 9)


def _strategy_decision_strength_rank(strength: str) -> int:
    return {
        "HIGH_PRUNE": 0,
        "REVIEW_SOON": 1,
        "REVIEW": 2,
        "STRONG_KEEP": 3,
        "BORDERLINE_KEEP": 4,
        "MONITOR_ONLY": 5,
        "Clearly better": 6,
        "Marginally better": 7,
        "Tied with baseline": 8,
        "Needs more samples": 9,
        "Worse due to faster exit": 10,
        "Worse": 11,
    }.get(str(strength or "").strip(), 12)


def _select_strategy_best_candidate(
    compare_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not compare_rows:
        return None, None
    baseline = next(
        (dict(row) for row in compare_rows if str(row.get("variant") or "") == "CURRENT"),
        dict(compare_rows[0]),
    )
    best_candidate = next(
        (dict(row) for row in compare_rows if str(row.get("variant") or "") != "CURRENT"),
        None,
    )
    return baseline, best_candidate


def _classify_strategy_decision_row(
    *,
    symbol: str,
    in_live_rules: bool,
    freshness: dict[str, Any],
    compare_rows: list[dict[str, Any]],
    tuning_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_symbol = str(symbol).strip()
    freshness_status = str(freshness.get("status") or "Missing")
    tuning_payload = dict(tuning_row or {})
    baseline_row, best_candidate_row = _select_strategy_best_candidate(compare_rows)

    baseline_pnl = (
        float(baseline_row.get("total_pnl_thb", 0.0) or 0.0)
        if baseline_row
        else 0.0
    )
    best_candidate = best_candidate_row or baseline_row or {}
    best_variant = str(best_candidate.get("variant") or "n/a")
    compare_verdict = (
        str(best_candidate.get("decision") or "No compare result")
        if best_candidate_row
        else "No compare result"
    )
    best_pnl = float(best_candidate.get("total_pnl_thb", 0.0) or 0.0)
    best_edge_vs_baseline = best_pnl - baseline_pnl
    fee_guardrail = str(best_candidate.get("fee_guardrail") or "")
    blocking_warning = (
        freshness_status in {"Missing", "Stale"}
        or compare_verdict == "Needs more samples"
        or fee_guardrail in {"THIN_EDGE", "LOSS_AFTER_FEES"}
    )
    tuning_recommendation = str(tuning_payload.get("recommendation") or "")
    strength = (
        str(tuning_payload.get("confidence") or "").strip()
        or str(best_candidate.get("decision") or "").strip()
        or "n/a"
    )

    if freshness_status in {"Missing", "Stale"}:
        recommended_action = "Sync first"
        action_reason = f"{freshness_status} candle data for the selected compare window."
    elif (
        not in_live_rules
        and compare_verdict == "Clearly better"
        and not blocking_warning
    ):
        recommended_action = "Promote"
        action_reason = f"{best_variant} clearly beats CURRENT without a blocking compare warning."
    elif (
        tuning_recommendation == "PRUNE"
        or str(tuning_payload.get("confidence") or "") == "HIGH_PRUNE"
        or fee_guardrail == "LOSS_AFTER_FEES"
        or (
            compare_verdict in {"Worse", "Worse due to faster exit"}
            and baseline_pnl <= 0.0
        )
        or (best_pnl <= 0.0 and baseline_pnl <= 0.0)
    ):
        recommended_action = "Prune candidate"
        if tuning_recommendation == "PRUNE":
            action_reason = "Live tuning already flags this symbol as PRUNE."
        elif fee_guardrail == "LOSS_AFTER_FEES":
            action_reason = "Compare edge is non-profitable after fees."
        else:
            action_reason = "Compare outcome is weak enough that keeping this symbol active is hard to justify."
    else:
        recommended_action = "Keep"
        if compare_verdict == "Clearly better":
            action_reason = "Candidate looks better, but existing warnings make promotion premature."
        elif compare_verdict in {"Tied with baseline", "Marginally better", "No compare result"}:
            action_reason = "Baseline still looks acceptable relative to the current evidence."
        else:
            action_reason = "Current symbol setup is acceptable enough to keep watching."

    return {
        "symbol": normalized_symbol,
        "in_live_rules": "YES" if in_live_rules else "NO",
        "freshness_status": freshness_status,
        "last_candle_used": str(freshness.get("last_timestamp_text") or "Missing"),
        "best_candidate": best_variant,
        "compare_verdict": compare_verdict,
        "strength": strength,
        "recommended_action": recommended_action,
        "action_reason": action_reason,
        "tuning_recommendation": tuning_recommendation or "n/a",
        "best_pnl_thb": best_pnl,
        "baseline_pnl_thb": baseline_pnl,
        "edge_vs_baseline_thb": best_edge_vs_baseline,
        "blocking_warning": "YES" if blocking_warning else "NO",
        "freshness_warning": str(freshness.get("warning") or "").strip() or "n/a",
        "action_rank": _strategy_decision_action_rank(recommended_action),
        "freshness_rank": _strategy_decision_freshness_rank(freshness_status),
        "strength_rank": _strategy_decision_strength_rank(strength),
    }


def _summarize_strategy_decision_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {
        "Promote": 0,
        "Keep": 0,
        "Prune candidate": 0,
        "Sync first": 0,
    }
    for row in rows:
        action = str(row.get("recommended_action") or "")
        if action in counts:
            counts[action] += 1
    return counts


def _classify_prune_strength(row: dict[str, Any]) -> str:
    """Returns 'Strong prune' when two or more signals align, otherwise 'Review prune'."""
    is_high_prune = str(row.get("strength") or "") == "HIGH_PRUNE"
    is_tuning_prune = str(row.get("tuning_recommendation") or "") == "PRUNE"
    both_pnl_negative = (
        float(row.get("best_pnl_thb", 0.0) or 0.0) <= 0.0
        and float(row.get("baseline_pnl_thb", 0.0) or 0.0) <= 0.0
    )
    has_blocking_warning = str(row.get("blocking_warning") or "") == "YES"
    signal_count = sum([is_high_prune, is_tuning_prune, both_pnl_negative, has_blocking_warning])
    return "Strong prune" if signal_count >= 2 else "Review prune"


def _build_strategy_action_preview(row: dict[str, Any]) -> dict[str, Any]:
    action = str(row.get("recommended_action") or "")
    prune_grade: str | None = None
    if action == "Prune candidate":
        prune_grade = _classify_prune_strength(row)
    suggested_page = {
        "Promote": "Compare",
        "Prune candidate": "Live Tuning",
        "Sync first": "Sync & Rank",
        "Keep": "Compare",
    }.get(action, "Compare")
    return {
        "type": "single",
        "symbol": str(row.get("symbol") or ""),
        "recommended_action": action,
        "prune_grade": prune_grade,
        "freshness": str(row.get("freshness_status") or ""),
        "last_candle_used": str(row.get("last_candle_used") or "n/a"),
        "in_live_rules": str(row.get("in_live_rules") or "NO"),
        "best_candidate": str(row.get("best_candidate") or "n/a"),
        "compare_verdict": str(row.get("compare_verdict") or ""),
        "strength": str(row.get("strength") or "n/a"),
        "reason": str(row.get("action_reason") or ""),
        "baseline_pnl_thb": float(row.get("baseline_pnl_thb", 0.0) or 0.0),
        "best_pnl_thb": float(row.get("best_pnl_thb", 0.0) or 0.0),
        "edge_vs_baseline_thb": float(row.get("edge_vs_baseline_thb", 0.0) or 0.0),
        "suggested_next_page": suggested_page,
    }


def _build_strategy_batch_sync_preview(
    sync_rows: list[dict[str, Any]],
    *,
    resolution: str,
    days: int,
) -> dict[str, Any]:
    symbols = [str(r.get("symbol") or "") for r in sync_rows if r.get("symbol")]
    freshness_counts: dict[str, int] = {}
    for row in sync_rows:
        status = str(row.get("freshness_status") or "Missing")
        freshness_counts[status] = freshness_counts.get(status, 0) + 1
    parts: list[str] = []
    if freshness_counts.get("Missing", 0):
        parts.append(f"{freshness_counts['Missing']} missing")
    if freshness_counts.get("Stale", 0):
        parts.append(f"{freshness_counts['Stale']} stale")
    sync_reason = (
        f"{', '.join(parts)} — Compare results are blocked until refreshed."
        if parts
        else "Candle data needs a refresh before Compare or classification can run."
    )
    return {
        "type": "batch_sync",
        "symbol_count": len(symbols),
        "symbols": symbols,
        "resolution": str(resolution),
        "days": int(days),
        "sync_reason": sync_reason,
        "freshness_counts": dict(freshness_counts),
        "suggested_next_page": "Sync & Rank",
    }


def _render_strategy_action_preview(preview: dict[str, Any]) -> None:
    ptype = str(preview.get("type") or "single")

    if ptype == "batch_sync":
        symbol_count = int(preview.get("symbol_count") or 0)
        symbols = list(preview.get("symbols") or [])
        resolution = str(preview.get("resolution") or "")
        days = int(preview.get("days") or 0)
        sync_reason = str(preview.get("sync_reason") or "")
        render_callout(
            f"Batch sync  ·  {symbol_count} symbol(s)  →  Sync & Rank",
            f"{sync_reason}  Resolution: {resolution}, lookback {days}d.  Symbols: {', '.join(symbols)}",
            "warn",
        )
        return

    action = str(preview.get("recommended_action") or "")
    symbol = str(preview.get("symbol") or "")
    prune_grade = str(preview.get("prune_grade") or "")
    label_action = prune_grade if prune_grade else action
    freshness = str(preview.get("freshness") or "")
    next_page = str(preview.get("suggested_next_page") or "")
    last_candle = str(preview.get("last_candle_used") or "n/a")
    in_live_rules = str(preview.get("in_live_rules") or "NO")
    best_candidate = str(preview.get("best_candidate") or "n/a")
    compare_verdict = str(preview.get("compare_verdict") or "")
    strength = str(preview.get("strength") or "n/a")
    reason = str(preview.get("reason") or "")
    edge = float(preview.get("edge_vs_baseline_thb") or 0.0)
    base_pnl = float(preview.get("baseline_pnl_thb") or 0.0)
    best_pnl = float(preview.get("best_pnl_thb") or 0.0)

    tone = {"Promote": "good", "Prune candidate": "warn", "Sync first": "bad", "Keep": "info"}.get(action, "info")
    render_callout(
        f"Preview  ·  {symbol}  →  {next_page}",
        f"{label_action}  ·  {freshness}",
        tone,
    )
    pcol1, pcol2, pcol3 = st.columns(3)
    with pcol1:
        render_metric_card("Last candle", last_candle, f"Live rule: {in_live_rules}")
    with pcol2:
        render_metric_card("Best candidate", best_candidate, compare_verdict)
    with pcol3:
        render_metric_card("Strength", strength, f"Edge {edge:+.2f} THB")
    if reason:
        st.caption(f"Reason: {reason}")
    if base_pnl != 0.0 or best_pnl != 0.0:
        st.caption(f"PnL: baseline {base_pnl:+.2f} THB → best {best_pnl:+.2f} THB")


def _group_strategy_decision_queue(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "Sync first": [],
        "Promote": [],
        "Prune candidate": [],
        "Keep": [],
    }
    for row in rows:
        action = str(row.get("recommended_action") or "")
        if action in groups:
            groups[action].append(row)
    return groups


def _render_strategy_decision_queue(
    decision_rows: list[dict[str, Any]],
    *,
    decision_resolution: str,
    decision_days: int,
) -> None:
    render_section_intro(
        "Decision Queue",
        "Symbols grouped by verdict — act on each bucket without opening them one at a time. Highest-priority groups appear first.",
        "Queue",
    )
    groups = _group_strategy_decision_queue(decision_rows)

    def _compact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "symbol": row.get("symbol", ""),
                "freshness": row.get("freshness_status", ""),
                "best_candidate": row.get("best_candidate", ""),
                "verdict": row.get("compare_verdict", ""),
                "reason": row.get("action_reason", ""),
                "last_candle": row.get("last_candle_used", ""),
            }
            for row in rows
        ]

    def _compact_prune_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "symbol": row.get("symbol", ""),
                "grade": _classify_prune_strength(row),
                "freshness": row.get("freshness_status", ""),
                "best_candidate": row.get("best_candidate", ""),
                "verdict": row.get("compare_verdict", ""),
                "reason": row.get("action_reason", ""),
                "last_candle": row.get("last_candle_used", ""),
            }
            for row in rows
        ]

    # --- Sync first ---
    sync_rows = groups["Sync first"]
    with st.expander(f"Sync First  ·  {len(sync_rows)}", expanded=bool(sync_rows)):
        st.caption("Missing or stale candle data — sync before running Compare or deciding.")
        if sync_rows:
            st.dataframe(_compact_rows(sync_rows), hide_index=True, width="stretch")
            stale_symbols = [str(r.get("symbol") or "") for r in sync_rows if r.get("symbol")]
            _render_strategy_action_preview(
                _build_strategy_batch_sync_preview(
                    sync_rows, resolution=decision_resolution, days=decision_days
                )
            )
            if st.button(
                f"Sync These Now — {len(stale_symbols)} symbol(s)",
                key="decision_queue_sync_all_btn",
                type="primary",
                help="Pre-fills the symbol list in Sync & Rank and switches to that tab. You will still need to click Sync Selected Symbols to execute.",
            ):
                st.session_state["strategy_queue_sync_symbols"] = stale_symbols
                queue_strategy_workspace_navigation(workspace="Sync & Rank")
                st.rerun()
        else:
            st.caption("All symbols have usable data.")

    # --- Promote ---
    promote_rows = groups["Promote"]
    promote_symbols = [str(r.get("symbol") or "") for r in promote_rows if r.get("symbol")]
    cached_promote = str(st.session_state.get("decision_queue_promote_select") or "")
    if promote_symbols and cached_promote not in promote_symbols:
        st.session_state["decision_queue_promote_select"] = promote_symbols[0]
        cached_promote = promote_symbols[0]

    with st.expander(f"Promote  ·  {len(promote_rows)}", expanded=bool(promote_rows)):
        st.caption("Fresh data, variant clearly beats CURRENT. Ready to add to live rules.")
        if promote_rows:
            st.dataframe(_compact_rows(promote_rows), hide_index=True, width="stretch")
            promote_by_symbol = {str(r.get("symbol") or ""): r for r in promote_rows}
            if cached_promote and cached_promote in promote_by_symbol:
                _render_strategy_action_preview(
                    _build_strategy_action_preview(promote_by_symbol[cached_promote])
                )
            qcol1, qcol2 = st.columns([1, 1])
            with qcol1:
                if st.button("Open next promote-ready", key="decision_queue_open_promote_btn", type="primary"):
                    queue_strategy_workspace_navigation(workspace="Compare", symbol=promote_symbols[0])
                    st.session_state["strategy_decision_context"] = str(promote_rows[0].get("action_reason") or "")
                    st.rerun()
            with qcol2:
                selected_promote = st.selectbox(
                    "Symbol",
                    promote_symbols,
                    key="decision_queue_promote_select",
                    label_visibility="collapsed",
                )
                if st.button("Open compare", key="decision_queue_open_promote_compare_btn"):
                    target_promote = str(selected_promote or promote_symbols[0])
                    queue_strategy_workspace_navigation(workspace="Compare", symbol=target_promote)
                    st.session_state["strategy_decision_context"] = str(promote_by_symbol.get(target_promote, {}).get("action_reason") or "")
                    st.rerun()
        else:
            st.caption("No promote-ready symbols yet.")

    # --- Prune candidate ---
    prune_rows = groups["Prune candidate"]
    prune_symbols = [str(r.get("symbol") or "") for r in prune_rows if r.get("symbol")]
    cached_prune = str(st.session_state.get("decision_queue_prune_select") or "")
    if prune_symbols and cached_prune not in prune_symbols:
        st.session_state["decision_queue_prune_select"] = prune_symbols[0]
        cached_prune = prune_symbols[0]

    prune_grade_counts: dict[str, int] = {}
    for r in prune_rows:
        grade = _classify_prune_strength(r)
        prune_grade_counts[grade] = prune_grade_counts.get(grade, 0) + 1
    strong_count = prune_grade_counts.get("Strong prune", 0)
    review_count = prune_grade_counts.get("Review prune", 0)
    prune_label = f"Prune Candidate  ·  {len(prune_rows)}"
    if strong_count > 0 and review_count > 0:
        prune_label += f"  ({strong_count} strong / {review_count} review)"
    elif strong_count > 0:
        prune_label += f"  ({strong_count} strong)"

    with st.expander(prune_label, expanded=bool(prune_rows)):
        st.caption("Weak or negative edge on live rules. Review in Live Tuning before removing.")
        if prune_rows:
            st.dataframe(_compact_prune_rows(prune_rows), hide_index=True, width="stretch")
            prune_by_symbol = {str(r.get("symbol") or ""): r for r in prune_rows}
            if cached_prune and cached_prune in prune_by_symbol:
                _render_strategy_action_preview(
                    _build_strategy_action_preview(prune_by_symbol[cached_prune])
                )
            qcol3, qcol4 = st.columns([1, 1])
            with qcol3:
                if st.button("Open next prune candidate", key="decision_queue_open_prune_btn"):
                    queue_strategy_workspace_navigation(workspace="Live Tuning", symbol=prune_symbols[0])
                    st.session_state["strategy_decision_context"] = str(prune_rows[0].get("action_reason") or "")
                    st.rerun()
            with qcol4:
                selected_prune = st.selectbox(
                    "Symbol",
                    prune_symbols,
                    key="decision_queue_prune_select",
                    label_visibility="collapsed",
                )
                if st.button("Open live tuning", key="decision_queue_open_prune_tuning_btn"):
                    target_prune = str(selected_prune or prune_symbols[0])
                    queue_strategy_workspace_navigation(workspace="Live Tuning", symbol=target_prune)
                    st.session_state["strategy_decision_context"] = str(prune_by_symbol.get(target_prune, {}).get("action_reason") or "")
                    st.rerun()
        else:
            st.caption("No prune candidates right now.")

    # --- Keep ---
    keep_rows = groups["Keep"]
    with st.expander(f"Keep  ·  {len(keep_rows)}", expanded=False):
        st.caption("Baseline acceptable. No immediate action needed.")
        if keep_rows:
            st.dataframe(_compact_rows(keep_rows), hide_index=True, width="stretch")
        else:
            st.caption("No symbols in Keep state.")


def _sort_strategy_decision_rows(
    rows: list[dict[str, Any]],
    *,
    sort_mode: str,
) -> list[dict[str, Any]]:
    normalized_mode = str(sort_mode or "Recommended Action")
    if normalized_mode == "Symbol":
        return sorted(rows, key=lambda row: str(row.get("symbol") or ""))
    if normalized_mode == "Freshness":
        return sorted(
            rows,
            key=lambda row: (
                int(row.get("freshness_rank", 9)),
                int(row.get("action_rank", 9)),
                str(row.get("symbol") or ""),
            ),
        )
    if normalized_mode == "Best Edge":
        return sorted(
            rows,
            key=lambda row: (
                -float(row.get("edge_vs_baseline_thb", 0.0) or 0.0),
                int(row.get("action_rank", 9)),
                str(row.get("symbol") or ""),
            ),
        )
    return sorted(
        rows,
        key=lambda row: (
            int(row.get("action_rank", 9)),
            int(row.get("freshness_rank", 9)),
            int(row.get("strength_rank", 99)),
            -float(row.get("edge_vs_baseline_thb", 0.0) or 0.0),
            str(row.get("symbol") or ""),
        ),
    )


def _build_strategy_decision_summary_rows(
    *,
    config: dict[str, Any],
    candidate_symbols: list[str],
    decision_resolution: str,
    decision_days: int,
    tuning_rows: list[dict[str, Any]],
    ranking_last_close_by_symbol: dict[str, float],
    session_state: MutableMapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    state = session_state if session_state is not None else st.session_state
    tuning_by_symbol = {
        str(row.get("symbol") or ""): dict(row)
        for row in tuning_rows
        if str(row.get("symbol") or "").strip()
    }

    summary_rows: list[dict[str, Any]] = []
    for symbol in candidate_symbols:
        normalized_symbol = str(symbol).strip()
        if not normalized_symbol:
            continue
        freshness = _build_compare_data_freshness(
            source="candles",
            resolution=str(decision_resolution),
            last_timestamp=_load_compare_data_last_timestamp(
                symbol=normalized_symbol,
                source="candles",
                resolution=str(decision_resolution),
            ),
        )
        compare_rows: list[dict[str, Any]] = []
        if str(freshness.get("status") or "") not in {"Missing", "Stale"}:
            base_rule = build_rule_seed(
                config,
                normalized_symbol,
                market_price=ranking_last_close_by_symbol.get(normalized_symbol),
            )
            compare_variants = build_rule_compare_variants(base_rule=base_rule)
            compare_rows = annotate_strategy_compare_rows(
                run_strategy_compare_rows(
                    symbol=normalized_symbol,
                    replay_source="candles",
                    replay_resolution=str(decision_resolution),
                    lookback_days=int(decision_days),
                    fee_rate=float(config["fee_rate"]),
                    cooldown_seconds=int(config["cooldown_seconds"]),
                    variants=compare_variants,
                    cache_token=_strategy_compare_cache_token(
                        session_state=state,
                        symbol=normalized_symbol,
                        source="candles",
                        resolution=str(decision_resolution),
                    ),
                )
            )
        summary_rows.append(
            _classify_strategy_decision_row(
                symbol=normalized_symbol,
                in_live_rules=normalized_symbol in config.get("rules", {}),
                freshness=freshness,
                compare_rows=compare_rows,
                tuning_row=tuning_by_symbol.get(normalized_symbol),
            )
        )
    return summary_rows


def _render_live_rule_price_overlay(
    *,
    symbol: str,
    buy_below: float,
    sell_above: float,
    latest_prices: dict[str, float],
    quote_fetched_at: str | None,
) -> None:
    live_price = float(latest_prices.get(str(symbol), 0.0) or 0.0)
    if live_price <= 0:
        st.caption(
            "Live price overlay: market price is unavailable for this symbol right now."
        )
        return

    buy_gap_percent = ((buy_below - live_price) / live_price) * 100.0
    sell_gap_percent = ((sell_above - live_price) / live_price) * 100.0
    st.caption(
        "Live price overlay: "
        f"price={live_price:,.8f} | "
        f"buy_below={buy_below:,.8f} ({buy_gap_percent:+.2f}%) | "
        f"sell_above={sell_above:,.8f} ({sell_gap_percent:+.2f}%)"
    )

    if quote_fetched_at:
        try:
            quote_age_seconds = max(
                0.0,
                (now_dt() - parse_time_text(str(quote_fetched_at))).total_seconds(),
            )
            freshness = "fresh" if quote_age_seconds <= 30.0 else "stale"
            st.caption(
                f"Live quote freshness: {freshness} ({quote_age_seconds:.0f}s old)"
            )
        except Exception:
            st.caption(
                f"Live quote freshness: timestamp unavailable ({quote_fetched_at})"
            )
    else:
        st.caption("Live quote freshness: unavailable.")


@st.cache_data(ttl=30, show_spinner=False)
def _cached_trade_analytics(symbol_key: str = "") -> dict[str, Any]:
    return fetch_trade_analytics(symbol=symbol_key or None)


@st.cache_data(ttl=30, show_spinner=False)
def _cached_market_snapshot_coverage(days: int) -> list[dict[str, Any]]:
    return fetch_market_snapshot_coverage(days=days)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_reports_page_payload(
    *,
    today: str,
    days: int,
    symbol_key: str = "",
) -> dict[str, Any]:
    return fetch_reports_page_dataset(
        today=today,
        days=days,
        symbol=symbol_key or None,
    )


@st.cache_data(ttl=60, show_spinner=False)
def _cached_coin_ranking(
    *,
    symbols: tuple[str, ...],
    resolution: str,
    lookback_days: int,
) -> dict[str, Any]:
    return build_coin_ranking(
        symbols=list(symbols),
        resolution=resolution,
        lookback_days=lookback_days,
    )


@st.cache_data(ttl=20, show_spinner=False)
def _cached_strategy_tuning_history(limit: int = 8) -> list[dict[str, Any]]:
    return fetch_runtime_event_log(limit=limit, event_type="strategy_tuning")


@st.cache_data(ttl=20, show_spinner=False)
def _cached_strategy_compare_selection_history(limit: int = 200) -> list[dict[str, Any]]:
    return fetch_runtime_event_log(limit=limit, event_type="strategy_compare_selection")


def _latest_strategy_compare_selection_map(limit: int = 200) -> dict[str, dict[str, Any]]:
    selections: dict[str, dict[str, Any]] = {}
    for row in _cached_strategy_compare_selection_history(limit=limit):
        details = dict(row.get("details") or {})
        scope = _strategy_compare_scope_key(
            symbol=str(details.get("symbol") or ""),
            source=str(details.get("source") or ""),
            resolution=str(details.get("resolution") or ""),
            days=int(details.get("days") or 0),
        )
        if scope.strip("|") and scope not in selections:
            selections[scope] = {
                "focus_variant": str(details.get("focus_variant") or ""),
                "created_at": str(row.get("created_at") or ""),
            }
    return selections


def _latest_strategy_compare_applied_map(limit: int = 200) -> dict[str, dict[str, Any]]:
    applied: dict[str, dict[str, Any]] = {}
    for row in _cached_strategy_tuning_history(limit=limit):
        details = dict(row.get("details") or {})
        if str(details.get("action") or "") != "apply_compared_variant":
            continue
        scope = _strategy_compare_scope_key(
            symbol=str(details.get("symbol") or ""),
            source=str(details.get("source") or ""),
            resolution=str(details.get("resolution") or ""),
            days=int(details.get("days") or 0),
        )
        if scope.strip("|") and scope not in applied:
            applied[scope] = {
                "variant": str(details.get("variant") or ""),
                "rule": dict(details.get("rule") or {}),
                "created_at": str(row.get("created_at") or ""),
            }
    return applied


@st.cache_data(ttl=20, show_spinner=False)
def _cached_auto_entry_review_events(limit: int = 40) -> list[dict[str, Any]]:
    return fetch_runtime_event_log(limit=limit, event_type="auto_live_entry_review")


def _build_auto_entry_review_report(limit: int = 40) -> dict[str, Any]:
    events = _cached_auto_entry_review_events(limit=limit)
    rejection_counts: dict[str, int] = defaultdict(int)
    symbol_reject_counts: dict[str, int] = defaultdict(int)
    symbol_candidate_counts: dict[str, int] = defaultdict(int)
    top_candidate_rows: list[dict[str, Any]] = []
    latest_context: dict[str, Any] = {}

    for index, row in enumerate(events):
        details = dict(row.get("details") or {})
        candidates = list(details.get("candidates") or [])
        rejected = list(details.get("rejected") or [])
        if index == 0:
            latest_context = dict(details.get("ranking_context") or {})
        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "")
            if symbol:
                symbol_candidate_counts[symbol] += 1
            top_candidate_rows.append(
                {
                    "created_at": row.get("created_at"),
                    "symbol": symbol or "n/a",
                    "ranking_score": float(candidate.get("ranking_score") or 0.0),
                    "entry_discount_percent": float(candidate.get("entry_discount_percent") or 0.0),
                    "trend_bias": str(candidate.get("trend_bias") or "n/a"),
                }
            )
        for rejected_row in rejected:
            symbol = str(rejected_row.get("symbol") or "")
            if symbol:
                symbol_reject_counts[symbol] += 1
            for reason in list(rejected_row.get("reasons") or []):
                rejection_counts[str(reason)] += 1

    top_candidate_rows.sort(
        key=lambda item: (
            -float(item.get("ranking_score") or 0.0),
            -float(item.get("entry_discount_percent") or 0.0),
            str(item.get("symbol") or ""),
        )
    )

    return {
        "events": events,
        "latest_context": latest_context,
        "rejection_summary": [
            {"reason": reason, "count": count}
            for reason, count in sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "symbol_reject_summary": [
            {"symbol": symbol, "rejections": count}
            for symbol, count in sorted(symbol_reject_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "symbol_candidate_summary": [
            {"symbol": symbol, "candidate_hits": count}
            for symbol, count in sorted(symbol_candidate_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "top_candidates": top_candidate_rows[:20],
    }


def _build_pruned_live_rules_config(
    *,
    config: dict[str, Any],
    symbols_to_prune: list[str],
    remove_from_watchlist: bool,
) -> dict[str, Any]:
    prune_set = {str(symbol) for symbol in symbols_to_prune if str(symbol).strip()}
    updated = dict(config)
    updated_rules = {
        symbol: rule
        for symbol, rule in dict(config.get("rules", {})).items()
        if symbol not in prune_set
    }
    updated["rules"] = updated_rules

    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", sorted(config.get("rules", {}).keys()))
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    if remove_from_watchlist:
        updated["watchlist_symbols"] = [
            symbol for symbol in watchlist_symbols if symbol not in prune_set
        ]
    else:
        updated["watchlist_symbols"] = ordered_unique_symbols(
            watchlist_symbols,
            updated_rules.keys(),
        )
    return updated


def render_sidebar(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any],
    selected_page: str,
    version_label: str,
    version_detail: str,
) -> str:
    with st.sidebar:
        render_sidebar_block(
            "Workspace",
            f"Mode <strong>{str(config['mode']).upper()}</strong><br>Config <code>{CONFIG_PATH}</code><br>SQLite <code>{DB_PATH}</code>",
        )
        render_sidebar_block(
            "Deployment",
            f"Version <strong>{version_label}</strong><br>{version_detail}",
        )
        st.markdown("### Navigation")
        if st.session_state.get("sidebar_page") not in PAGE_ORDER:
            st.session_state["sidebar_page"] = selected_page
        page_name = st.radio(
            "Page",
            PAGE_ORDER,
            key="sidebar_page",
            label_visibility="collapsed",
        )
        render_sidebar_block(
            "Current Page",
            f"<strong>{page_name}</strong><br>Use Refresh Dashboard after config changes or when you want a full rerender.",
        )
        if st.button("Refresh Dashboard", width='stretch'):
            st.rerun()

        st.markdown("### Status")
        status_badges = [badge(f"Mode {str(config['mode']).upper()}", "info")]
        st.markdown(" ".join(status_badges), unsafe_allow_html=True)
        render_sidebar_block(
            "Private API",
            str(private_ctx["private_api_status"]),
        )
        for item in private_ctx["private_api_capabilities"]:
            tone = capability_badge_tone(item)
            st.markdown(badge(item, tone), unsafe_allow_html=True)

        render_sidebar_block(
            "Guideline",
            "Overview = health first<br>Live Ops = real actions<br>Execution Assistant = adjust live rule prices against the current quote<br>Strategy = tune rules before widening automation<br>Logs = debug only when summary says something is wrong",
        )
    return str(page_name)


def render_strategy_page(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    latest_prices: dict[str, float] | None = None,
    quote_fetched_at: str | None = None,
) -> None:
    private_ctx = dict(private_ctx or {})
    runtime = dict(runtime or {})
    latest_prices = {
        str(symbol): float(price)
        for symbol, price in dict(latest_prices or {}).items()
        if str(symbol)
    }
    open_execution_orders = fetch_open_execution_orders()
    render_section_intro(
        "Strategy Lab",
        "Research first, then tighten live rules. Watchlist symbols are your research universe, while config rules remain the live shortlist.",
        "Research & Tuning",
    )
    render_section_intro("Strategy Workflow", "Follow this order to avoid overreacting to single metrics or isolated replays.", "Workflow")
    workflow_cols = st.columns(4)
    with workflow_cols[0]:
        render_metric_card("1. Sync & Rank", "Universe first", "Sync candles, then use Coin Ranking to see which symbols deserve attention.")
    with workflow_cols[1]:
        render_metric_card("2. Shortlist", "Gate live rules", "Check Auto Entry Shortlist and confirm which current rules still pass ranking filters.")
    with workflow_cols[2]:
        render_metric_card("3. Tune", "Keep / Review / Prune", "Use Live Rule Tuning to identify weak live rules before widening automation.")
    with workflow_cols[3]:
        render_metric_card("4. Compare", "Beat baseline", "Use Strategy Compare Lab to test variants, then apply the winner back into config.")
    st.caption(
        "Recommended order: Coin Ranking -> Auto Entry Shortlist -> Live Rule Tuning -> Strategy Compare Lab -> Replay Lab for manual deep-dive."
    )

    rank_resolution_options = ["1", "5", "15", "60", "240", "1D"]
    default_rank_resolution = str(st.session_state.get("strategy_rank_resolution", "240"))
    if default_rank_resolution not in rank_resolution_options:
        default_rank_resolution = "240"
    default_rank_days = int(st.session_state.get("strategy_rank_days", 14))
    ranking_resolution = str(st.session_state.get("strategy_rank_resolution", default_rank_resolution))
    ranking_days = int(st.session_state.get("strategy_rank_days", default_rank_days))
    configured_symbols = sorted(config["rules"].keys())
    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", configured_symbols)
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    coverage_days = int(
        config.get(
            "market_snapshot_hot_retention_days",
            config.get("market_snapshot_retention_days", 30),
        )
    )
    auto_entry_min_score = float(config.get("live_auto_entry_min_score", 50.0))
    auto_entry_allowed_biases = {
        str(value).strip().lower()
        for value in config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"])
        if str(value).strip()
    } or {"bullish", "mixed"}

    strategy_workspace_options = [
        "Decisions",
        "Sync & Rank",
        "Live Tuning",
        "Compare",
        "Replay",
        "Overview",
    ]
    _workspace_display_names = {
        "Decisions": "Decisions",
        "Sync & Rank": "Sync & Rank",
        "Live Tuning": "Live Rules",
        "Compare": "Compare Lab",
        "Replay": "Replay Lab",
        "Overview": "Analytics",
    }
    workspace_autorun = st.session_state.pop("strategy_workspace_autorun", None)
    queued_compare_symbol = st.session_state.pop("strategy_compare_symbol_autorun", None)
    queued_tuning_symbol = st.session_state.pop("strategy_tuning_focus_symbol_autorun", None)
    workspace_focus_symbol = st.session_state.pop("strategy_workspace_focus_symbol", None)
    default_strategy_workspace = str(st.session_state.get("strategy_workspace", "Decisions"))
    if workspace_autorun in strategy_workspace_options:
        default_strategy_workspace = str(workspace_autorun)
        st.session_state["strategy_workspace"] = default_strategy_workspace

    queued_compare_target = str(queued_compare_symbol or workspace_focus_symbol or "").strip()
    if (
        default_strategy_workspace == "Compare"
        and queued_compare_target
        and queued_compare_target in configured_symbols
    ):
        st.session_state["strategy_compare_autorun"] = {
            "symbol": queued_compare_target,
            "source": "candles",
            "resolution": ranking_resolution,
            "days": ranking_days,
        }
    queued_tuning_target = str(queued_tuning_symbol or workspace_focus_symbol or "").strip()
    if default_strategy_workspace == "Live Tuning" and queued_tuning_target:
        st.session_state["strategy_tuning_focus_symbol"] = queued_tuning_target
        st.session_state["strategy_tuning_focus_autorun"] = queued_tuning_target
    if default_strategy_workspace not in strategy_workspace_options:
        default_strategy_workspace = "Decisions"
    _sync_select_state(
        key="strategy_workspace",
        options=strategy_workspace_options,
        default=default_strategy_workspace,
    )
    strategy_workspace = st.radio(
        "Strategy Workspace",
        strategy_workspace_options,
        horizontal=True,
        key="strategy_workspace",
        format_func=lambda w: _workspace_display_names.get(w, w),
    )
    if strategy_workspace != "Decisions":
        render_callout(
            "Workspace Focus",
            {
                "Sync & Rank": "Sync candles, inspect ranking, and decide which symbols deserve live attention.",
                "Live Tuning": "Review live rules, fee guardrails, and auto-entry report. Use this when a symbol needs a prune decision.",
                "Compare": "Run variants for one live symbol and apply the winner back to config. Nothing changes until you click Apply.",
                "Replay": "Manual deep-dive for a single symbol with its own replay controls and coverage. Read-only.",
                "Overview": "Lightweight summary of actual paper-trade results. Read-only.",
            }[strategy_workspace],
            "info",
        )

    should_show_overview = strategy_workspace == "Overview"
    should_show_decisions = strategy_workspace == "Decisions"
    should_show_ranking = strategy_workspace == "Sync & Rank"
    should_show_tuning = strategy_workspace == "Live Tuning"
    should_show_compare = strategy_workspace == "Compare"
    should_show_replay = strategy_workspace == "Replay"
    should_show_decision_summary = should_show_decisions
    should_defer_tuning_ranking = should_show_tuning and bool(queued_tuning_target)
    decision_summary_resolution = str(
        st.session_state.get("strategy_compare_resolution", ranking_resolution)
    )
    if decision_summary_resolution not in rank_resolution_options:
        decision_summary_resolution = ranking_resolution
    decision_summary_days = int(
        st.session_state.get("strategy_compare_days", ranking_days)
    )
    decision_summary_days = min(max(decision_summary_days, 1), 90)

    market_universe = (
        fetch_market_symbol_universe()
        if (should_show_ranking or should_show_replay or should_show_decision_summary)
        else {"symbols": [], "error": None}
    )
    market_symbols = list(market_universe.get("symbols", []))
    symbols = market_symbols or watchlist_symbols or configured_symbols or ["THB_BTC"]

    ranking: dict[str, Any] = {"rows": [], "coverage": [], "errors": []}
    ranking_last_close_by_symbol: dict[str, float] = {}
    coverage_rows = _cached_market_snapshot_coverage(days=coverage_days) if should_show_replay else []
    decision_tuning_rows: list[dict[str, Any]] = []
    strategy_decision_rows: list[dict[str, Any]] = []

    if should_show_overview:
        analytics = _cached_trade_analytics()
        totals = analytics["totals"]

        card1, card2, card3, card4, card5 = st.columns(5)
        with card1:
            render_metric_card("Actual Trades", str(totals["trades"]), f"Win rate {totals['win_rate_percent']:.2f}%")
        with card2:
            render_metric_card("Actual PnL", f"{totals['total_pnl_thb']:,.2f} THB", f"Avg/trade {totals['avg_pnl_thb']:,.2f}")
        with card3:
            render_metric_card("Profit Factor", f"{totals['profit_factor']:.2f}", f"Avg win {totals['avg_win_thb']:,.2f}")
        with card4:
            render_metric_card("Hold Time", f"{totals['avg_hold_minutes']:.1f} min", f"Losses {totals['losses']}")
        with card5:
            render_metric_card("Actual Fees", f"{totals.get('total_fee_thb', 0.0):,.2f} THB", f"Fee drag {totals.get('fee_drag_percent', 0.0):.2f}%")

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
        top_left, top_right = st.columns([1.15, 0.85])
        with top_left:
            st.markdown('<div class="panel-title">Actual Trade Analytics by Symbol</div>', unsafe_allow_html=True)
            if analytics["by_symbol"]:
                st.dataframe(analytics["by_symbol"], width='stretch', hide_index=True)
            else:
                st.caption("No paper trade history exists yet. Run the paper bot longer or import prior trade logs first.")
        with top_right:
            st.markdown('<div class="panel-title">Exit Reason Breakdown</div>', unsafe_allow_html=True)
            if analytics["by_exit_reason"]:
                st.dataframe(analytics["by_exit_reason"], width='stretch', hide_index=True)
            else:
                st.caption("No exit reasons are available because no paper trades have been stored yet.")

        with st.expander("Recent Actual Paper Trades", expanded=False):
            if analytics["recent_trades"]:
                st.dataframe(analytics["recent_trades"], width='stretch', hide_index=True)
            else:
                st.caption("No recent paper trades available.")

    if should_show_ranking or should_show_decision_summary or (should_show_tuning and not should_defer_tuning_ranking):
        ranking_symbol_pool = list(symbols if should_show_ranking else (configured_symbols or watchlist_symbols or symbols))
        ranking = _cached_coin_ranking(
            symbols=tuple(ranking_symbol_pool),
            resolution=ranking_resolution,
            lookback_days=ranking_days,
        )
        ranking_last_close_by_symbol = {
            str(row["symbol"]): float(row.get("last_close", 0.0) or 0.0)
            for row in ranking["rows"]
        }

    if should_show_decision_summary:
        decision_tuning_rows = build_live_rule_tuning_rows(
            config=config,
            ranking_rows=ranking["rows"],
            ranking_resolution=ranking_resolution,
            ranking_days=ranking_days,
        )
        promote_candidates = [
            str(row.get("symbol") or "")
            for row in ranking["rows"]
            if str(row.get("symbol") or "")
            and str(row.get("symbol") or "") not in config.get("rules", {})
            and float(row.get("score", 0.0) or 0.0) >= auto_entry_min_score
            and str(row.get("trend_bias") or "").strip().lower() in auto_entry_allowed_biases
        ][:5]
        decision_symbol_pool = ordered_unique_symbols(configured_symbols, promote_candidates)
        strategy_decision_rows = _build_strategy_decision_summary_rows(
            config=config,
            candidate_symbols=decision_symbol_pool,
            decision_resolution=decision_summary_resolution,
            decision_days=decision_summary_days,
            tuning_rows=decision_tuning_rows,
            ranking_last_close_by_symbol=ranking_last_close_by_symbol,
            session_state=st.session_state,
        )

        render_section_intro(
            "Decision Summary",
            "Use this layer to decide which symbols need fresh data first, which ones are strong enough to promote, and which live rules look weak enough to cut back.",
            "Priority",
        )
        st.caption(
            f"Decision Summary uses candle compare at {decision_summary_resolution} over {decision_summary_days} day(s) for live rules plus the top promotable shortlist."
        )
        decision_counts = _summarize_strategy_decision_counts(strategy_decision_rows)
        decision_cards = st.columns(4)
        with decision_cards[0]:
            render_metric_card("Promote", str(decision_counts["Promote"]), "Fresh and clearly better than CURRENT")
        with decision_cards[1]:
            render_metric_card("Keep", str(decision_counts["Keep"]), "Baseline still acceptable")
        with decision_cards[2]:
            render_metric_card("Prune Candidate", str(decision_counts["Prune candidate"]), "Weak edge or repeated underperformance")
        with decision_cards[3]:
            render_metric_card("Sync First", str(decision_counts["Sync first"]), "Missing or stale candle compare data")

        decision_sort_options = [
            "Recommended Action",
            "Freshness",
            "Best Edge",
            "Symbol",
        ]
        _sync_select_state(
            key="strategy_decision_summary_sort",
            options=decision_sort_options,
            default="Recommended Action",
        )
        selected_decision_sort = st.selectbox(
            "Decision Summary Sort",
            decision_sort_options,
            key="strategy_decision_summary_sort",
        )
        sorted_decision_rows = _sort_strategy_decision_rows(
            strategy_decision_rows,
            sort_mode=str(selected_decision_sort),
        )
        if sorted_decision_rows:
            st.dataframe(
                [
                    {
                        "symbol": row["symbol"],
                        "live_rule": row["in_live_rules"],
                        "freshness": row["freshness_status"],
                        "recommended_action": row["recommended_action"],
                        "action_reason": row["action_reason"],
                    }
                    for row in sorted_decision_rows
                ],
                width='stretch',
                hide_index=True,
            )
        else:
            st.caption("No Strategy Decision Summary rows are available yet. Sync candles or add live rules first.")

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
        _render_strategy_decision_queue(
            sorted_decision_rows,
            decision_resolution=decision_summary_resolution,
            decision_days=decision_summary_days,
        )

    if should_show_ranking:
        st.markdown('<div class="panel-title">Candle Sync & Coin Ranking</div>', unsafe_allow_html=True)
        st.caption(
            "Sync TradingView history into SQLite first, then rank coins by recent momentum, range position, stability, and average volume."
        )

        if market_universe.get("error"):
            st.warning(f"Bitkub market symbols unavailable right now: {market_universe['error']}")
        if market_symbols:
            st.caption(
                f"Market universe loaded: {len(market_symbols)} symbol(s) | watchlist: {len(watchlist_symbols)} | live rules: {len(configured_symbols)}"
            )
        elif watchlist_symbols or configured_symbols:
            st.caption(
                f"Using local symbol list only | watchlist: {len(watchlist_symbols)} | live rules: {len(configured_symbols)}"
            )

        if "strategy_queue_sync_symbols" in st.session_state:
            queued_stale = [
                s for s in st.session_state.pop("strategy_queue_sync_symbols")
                if s in symbols
            ]
            if queued_stale:
                st.session_state["strategy_sync_symbol_select"] = queued_stale
        if "strategy_sync_symbol_select" not in st.session_state:
            st.session_state["strategy_sync_symbol_select"] = (
                watchlist_symbols or configured_symbols or symbols[: min(len(symbols), 10)]
            )

        st.caption("Step 1 — select symbols and resolution below   ·   Step 2 — click Sync Selected Symbols to execute")
        with st.form("strategy_candle_sync_form"):
            sync_col1, sync_col2, sync_col3 = st.columns([0.4, 0.3, 0.3])
            with sync_col1:
                selected_sync_symbols = st.multiselect(
                    "Symbols to Sync",
                    symbols,
                    key="strategy_sync_symbol_select",
                    help="Options come from the full Bitkub market universe when available. The default selection still follows the watchlist.",
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
            run_candle_sync = st.form_submit_button(
                "Sync Candles",
                type="primary",
                width='stretch',
                help="Fetches candle history for every symbol selected above. This writes to the local candle store.",
            )

        if run_candle_sync:
            sync_result = sync_candles_for_symbols(
                symbols=selected_sync_symbols or symbols,
                resolution=str(ranking_resolution),
                days=int(ranking_days),
            )
            invalidated_compare_scopes = _invalidate_strategy_compare_state_for_candle_sync(
                sync_result=sync_result,
            )
            if invalidated_compare_scopes:
                sync_result = dict(sync_result)
                sync_result["invalidated_compare_scopes"] = invalidated_compare_scopes
            st.session_state["strategy_candle_sync_result"] = sync_result
            st.session_state["strategy_rank_resolution"] = str(ranking_resolution)
            st.session_state["strategy_rank_days"] = int(ranking_days)
            st.rerun()

        sync_feedback = st.session_state.get("strategy_candle_sync_result")
        if sync_feedback:
            if sync_feedback.get("synced"):
                st.success(
                    f"Synced candles for {len(sync_feedback['synced'])} symbol(s) | resolution={sync_feedback['resolution']} | days={sync_feedback['days']}"
                )
                with st.expander("Sync Result Details", expanded=False):
                    st.dataframe(sync_feedback["synced"], width='stretch', hide_index=True)
                invalidated_compare_scopes = list(sync_feedback.get("invalidated_compare_scopes") or [])
                if invalidated_compare_scopes:
                    st.caption(
                        f"Invalidated compare cache for {len(invalidated_compare_scopes)} matching candle compare scope(s)."
                    )
            if sync_feedback.get("errors"):
                summarized_sync_errors = _summarize_text_lines(list(sync_feedback["errors"]))
                no_data_count = sum(row["count"] for row in summarized_sync_errors if "history status=no_data" in str(row["message"]))
                st.warning(
                    f"Sync warnings: {len(sync_feedback['errors'])} total | no_data {no_data_count}"
                )
                with st.expander("Sync Warning Summary", expanded=False):
                    st.dataframe(summarized_sync_errors, width='stretch', hide_index=True)

        rank_card1, rank_card2, rank_card3 = st.columns(3)
        with rank_card1:
            render_metric_card("Ranking Resolution", ranking_resolution, f"Lookback {ranking_days} day(s)")
        with rank_card2:
            render_metric_card("Ranked Symbols", str(len(ranking["rows"])), f"Coverage rows {len(ranking['coverage'])}")
        with rank_card3:
            top_score = ranking["rows"][0]["score"] if ranking["rows"] else 0.0
            render_metric_card("Top Score", f"{top_score:.2f}", "Higher = stronger trend shortlist")

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

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
        rank_left, rank_right = st.columns([1.15, 0.85])
        with rank_left:
            st.markdown('<div class="panel-title">Coin Ranking</div>', unsafe_allow_html=True)
            if ranking["rows"]:
                st.dataframe(ranking["rows"], width='stretch', hide_index=True)

                promotable_symbols = [
                    row["symbol"]
                    for row in ranking["rows"]
                    if row["symbol"] not in config["rules"]
                ]
                if promotable_symbols:
                    promotion_state_key = "strategy_promote_ranked_symbols"
                    default_promotions = (recommended_promotions or promotable_symbols)[
                        : min(len(recommended_promotions or promotable_symbols), 5)
                    ]
                    _sync_multiselect_state(
                        key=promotion_state_key,
                        options=promotable_symbols,
                        default=default_promotions,
                    )
                    with st.form("strategy_promote_ranked_symbols_form"):
                        selected_promotions = st.multiselect(
                            "Promote ranked symbols into live rules",
                            promotable_symbols,
                            key=promotion_state_key,
                            help="Adds a conservative starter rule for each selected ranked symbol and keeps them in the watchlist.",
                        )
                        submitted_promotions = st.form_submit_button(
                            "Promote to Live Rules",
                            width='stretch',
                            help="Adds a conservative starter rule for each selected symbol and saves to config. This mutates config.",
                        )
                    if submitted_promotions:
                        if not selected_promotions:
                            st.warning("Select at least one ranked symbol to promote.")
                        else:
                            updated = dict(config)
                            updated_rules = dict(config["rules"])
                            for promoted_symbol in selected_promotions:
                                if promoted_symbol not in updated_rules:
                                    updated_rules[promoted_symbol] = build_rule_seed(
                                        config,
                                        promoted_symbol,
                                        market_price=ranking_last_close_by_symbol.get(promoted_symbol),
                                    )
                            updated["rules"] = updated_rules
                            updated["watchlist_symbols"] = ordered_unique_symbols(
                                config.get("watchlist_symbols", configured_symbols),
                                selected_promotions,
                            )
                            if save_config_with_feedback(
                                config,
                                updated,
                                f"Promoted {len(selected_promotions)} ranked symbol(s) into live rules",
                            ):
                                st.session_state.pop(promotion_state_key, None)
                                st.session_state.pop(f"{promotion_state_key}__signature", None)
                                st.rerun()
                else:
                    st.info("All ranked symbols in the current shortlist already exist in live rules.")
            else:
                st.caption("No ranked symbols yet. Sync candles first or widen the lookback window.")
        with rank_right:
            st.markdown('<div class="panel-title">Stored Candle Coverage</div>', unsafe_allow_html=True)
            if ranking["coverage"]:
                st.dataframe(ranking["coverage"], width='stretch', hide_index=True)
            else:
                st.caption("No stored candles available yet.")
            if ranking.get("errors"):
                summarized_ranking_errors = _summarize_text_lines(list(ranking["errors"]))
                no_data_count = sum(row["count"] for row in summarized_ranking_errors if "not enough stored candles" in str(row["message"]))
                st.markdown('<div class="panel-title">Ranking Notes</div>', unsafe_allow_html=True)
                st.caption(f"{len(ranking['errors'])} note(s) | insufficient-candle rows {no_data_count}")
                with st.expander("Ranking Note Summary", expanded=False):
                    st.dataframe(summarized_ranking_errors, width='stretch', hide_index=True)

        st.markdown('<div class="panel-title">Auto Entry Shortlist</div>', unsafe_allow_html=True)
        st.caption(
            f"Shortlist uses current config filters: min_score >= {auto_entry_min_score:.1f}, biases = {', '.join(sorted(auto_entry_allowed_biases))}."
        )
        shortlist_left, shortlist_right = st.columns([1.0, 1.0])
        with shortlist_left:
            live_ready_rows = [row for row in shortlist_rows if row["recommendation"] == "LIVE_READY"]
            if live_ready_rows:
                st.dataframe(live_ready_rows[:12], width='stretch', hide_index=True)
            else:
                st.caption("No current live rules pass the auto-entry shortlist filters.")
        with shortlist_right:
            promote_rows = [row for row in shortlist_rows if row["recommendation"] == "PROMOTE"]
            if promote_rows:
                st.dataframe(promote_rows[:12], width='stretch', hide_index=True)
            else:
                st.caption("No extra watchlist symbols are currently strong enough to promote.")

    if should_show_tuning:
        _tuning_decision_context = st.session_state.pop("strategy_decision_context", None)
        if _tuning_decision_context:
            render_callout("From Decisions", _tuning_decision_context, "info")
        st.markdown('<div class="panel-title">Live Rule Tuning</div>', unsafe_allow_html=True)
        st.caption(
            "This section scores the current live rules against the active auto-entry gate and a candle replay using the same lookback window. "
            "Use it to decide which symbols to keep, review, or prune before widening live automation."
        )

        tuning_rows = (
            list(decision_tuning_rows)
            if decision_tuning_rows
            else build_live_rule_tuning_rows(
                config=config,
                ranking_rows=ranking["rows"],
                ranking_resolution=ranking_resolution,
                ranking_days=ranking_days,
            )
        )
        keep_rows = [row for row in tuning_rows if row["recommendation"] == "KEEP"]
        monitor_rows = [row for row in tuning_rows if row["recommendation"] == "MONITOR"]
        review_rows = [row for row in tuning_rows if row["recommendation"] == "REVIEW"]
        prune_rows = [row for row in tuning_rows if row["recommendation"] == "PRUNE"]
        strong_keep_count = sum(1 for row in keep_rows if row.get("confidence") == "STRONG_KEEP")
        high_prune_count = sum(1 for row in prune_rows if row.get("confidence") == "HIGH_PRUNE")
        fee_watch_count = sum(1 for row in tuning_rows if str(row.get("fee_guardrail")) in {"FEE_HEAVY", "THIN_EDGE", "LOSS_AFTER_FEES"})
        thin_edge_count = sum(1 for row in tuning_rows if str(row.get("fee_guardrail")) in {"THIN_EDGE", "LOSS_AFTER_FEES"})
        keep_count = len(keep_rows)
        monitor_count = len(monitor_rows)
        review_count = len(review_rows)
        prune_count = len(prune_rows)
        actionable_rows = prune_rows + review_rows + monitor_rows

        tuning_cards = st.columns(4)
        with tuning_cards[0]:
            render_metric_card("Live Rules Reviewed", str(len(tuning_rows)), f"Resolution {ranking_resolution}")
        with tuning_cards[1]:
            render_metric_card("Keep", str(keep_count), f"Strong keep {strong_keep_count} | Monitor {monitor_count}")
        with tuning_cards[2]:
            render_metric_card("Review", str(review_count), f"High prune {high_prune_count} | Prune {prune_count}")
        with tuning_cards[3]:
            total_replay_pnl = sum(float(row.get("replay_pnl_thb", 0.0) or 0.0) for row in tuning_rows)
            render_metric_card("Replay PnL Sum", f"{total_replay_pnl:,.2f} THB", f"Fee watch {fee_watch_count} | Thin edge {thin_edge_count}")

        if prune_rows:
            render_callout(
                "Live Rule Tuning",
                f"{prune_count} live rule(s) are currently marked PRUNE. Default move is to remove them from live rules and keep them in watchlist for research unless you intentionally opt out.",
                "bad",
            )
        elif review_rows:
            render_callout(
                "Live Rule Tuning",
                f"No hard prune candidates right now, but {review_count} symbol(s) should go through Compare Lab before you widen live auto-entry.",
                "warn",
            )
        else:
            render_callout(
                "Live Rule Tuning",
                "Live rules look stable right now. Focus on compare tests and auto-entry monitoring for incremental tuning.",
                "good",
            )

        tuning_left, tuning_right = st.columns([1.15, 0.85])
        with tuning_left:
            tuning_tabs = st.tabs(["Action Queue", "Full Matrix"])
            with tuning_tabs[0]:
                if actionable_rows:
                    action_queue_rows = []
                    for row in actionable_rows:
                        next_step = (
                            "Remove from live rules; keep in watchlist if you still want research coverage."
                            if row["recommendation"] == "PRUNE"
                            else "Run Compare Lab and update the rule before trusting wider automation."
                            if row["recommendation"] == "REVIEW"
                            else "Gather more replay trades before promoting or pruning this rule."
                        )
                        action_queue_rows.append(
                            {
                                "symbol": row["symbol"],
                                "recommendation": row["recommendation"],
                                "confidence": row["confidence"],
                                "market_context": row["market_context"],
                                "entry_gap_percent": row["entry_gap_percent"],
                                "target_gap_percent": row["target_gap_percent"],
                                "gate_reason": row["gate_reason"],
                                "replay_trades": row["replay_trades"],
                                "replay_pnl_thb": row["replay_pnl_thb"],
                                "replay_fee_drag_percent": row.get("replay_fee_drag_percent", 0.0),
                                "fee_guardrail": row.get("fee_guardrail", "n/a"),
                                "replay_win_rate": row["replay_win_rate"],
                                "next_step": next_step,
                            }
                        )
                    st.dataframe(action_queue_rows, width='stretch', hide_index=True)
                else:
                    st.caption("No action queue right now. All current live rules are either stable or still accumulating evidence.")

                if prune_rows:
                    st.markdown("**── Change config ──**")
                    prune_option_symbols = [row["symbol"] for row in prune_rows]
                    prune_default_symbols = list(prune_option_symbols)
                    prune_selection_key = "strategy_prune_live_rules_selection"
                    prune_remove_key = "strategy_prune_live_rules_quick_remove"
                    prune_add_key = "strategy_prune_live_rules_quick_add"
                    prune_action_key = "strategy_prune_live_rules_action"
                    prune_cancel_confirm_key = "strategy_prune_live_rules_confirm_cancel"

                    _sync_multiselect_state(
                        key=prune_selection_key,
                        options=prune_option_symbols,
                        default=prune_default_symbols,
                    )
                    current_prune_selection = list(
                        st.session_state.get(prune_selection_key, prune_default_symbols)
                    )
                    _sync_multiselect_state(
                        key=prune_remove_key,
                        options=current_prune_selection,
                        default=[],
                    )
                    _sync_multiselect_state(
                        key=prune_add_key,
                        options=[
                            symbol
                            for symbol in prune_option_symbols
                            if symbol not in current_prune_selection
                        ],
                        default=[],
                    )

                    with st.form("strategy_prune_live_rules_form"):
                        prune_selection = st.multiselect(
                            "Prune From Live Rules",
                            prune_option_symbols,
                            key=prune_selection_key,
                            help="Select the live-rule symbols you want to prune. If linked live orders exist, the form will force a review or an explicit cancel path.",
                        )
                        prune_remove_symbols = st.multiselect(
                            "Quick Remove From Current Selection",
                            prune_selection,
                            key=prune_remove_key,
                            help="Use the search box here to find a coin quickly and remove it from the prune list without scrolling through a long selected set.",
                        )
                        prune_add_symbols = st.multiselect(
                            "Quick Add Back To Selection",
                            [
                                symbol
                                for symbol in prune_option_symbols
                                if symbol not in prune_selection
                            ],
                            key=prune_add_key,
                            help="Use this if you removed too much and want to add a few symbols back without rebuilding the whole selection.",
                        )
                        remove_from_watchlist = st.checkbox(
                            "Also remove from watchlist",
                            value=False,
                            help="Leave this off if you still want ranking, replay, and research coverage for the symbol after pruning it from live rules.",
                        )

                        st.divider()
                        st.markdown("**Choose how to prune**")

                        effective_prune_selection = ordered_unique_symbols(
                            [
                                symbol
                                for symbol in prune_selection
                                if symbol not in set(prune_remove_symbols)
                            ],
                            prune_add_symbols,
                        )
                        linked_state_rows: list[dict[str, Any]] = []
                        unclear_symbols: list[str] = []
                        for symbol in effective_prune_selection:
                            state = build_symbol_operational_state(
                                symbol=symbol,
                                config=config,
                                account_snapshot=private_ctx.get("account_snapshot"),
                                latest_prices=latest_prices,
                                runtime=runtime,
                                execution_orders=open_execution_orders,
                            )
                            if (
                                state["open_buy_count"]
                                or state["open_sell_count"]
                                or state["reserved_thb"] > 0
                                or state["reserved_coin"] > 0
                            ):
                                linked_state_rows.append(
                                    {
                                        "symbol": symbol,
                                        "open_buy": state["open_buy_count"],
                                        "open_sell": state["open_sell_count"],
                                        "reserved_thb": state["reserved_thb"],
                                        "reserved_coin": state["reserved_coin"],
                                        "partial_fill": "YES" if state["partial_fill"] else "NO",
                                    }
                                )
                            if state["review_required"]:
                                unclear_symbols.append(symbol)

                        if linked_state_rows:
                            st.dataframe(linked_state_rows, width='stretch', hide_index=True)
                            st.caption(
                                "Linked live orders exist for the selected symbol(s). Choose a safe action below; review is required if any order state is unclear."
                            )

                        prune_action_options = ["Prune rule only"]
                        if linked_state_rows and not unclear_symbols:
                            prune_action_options = [
                                "Prune rule only",
                                "Cancel linked orders and prune",
                                "Review in Live Ops",
                            ]
                        elif linked_state_rows and unclear_symbols:
                            prune_action_options = ["Review in Live Ops"]

                        if prune_action_key not in st.session_state:
                            st.session_state[prune_action_key] = prune_action_options[0]
                        if str(st.session_state.get(prune_action_key)) not in prune_action_options:
                            st.session_state[prune_action_key] = prune_action_options[0]

                        prune_action = st.radio(
                            "Prune Action",
                            prune_action_options,
                            key=prune_action_key,
                            help="Cancel-linked pruning is only available when the symbol state is clear enough to proceed.",
                        )
                        confirm_cancel_linked = False
                        if prune_action == "Cancel linked orders and prune":
                            confirm_cancel_linked = st.checkbox(
                                "I understand linked live orders will be canceled first.",
                                key=prune_cancel_confirm_key,
                            )
                        prune_submitted = st.form_submit_button(
                            "Continue",
                            type="primary",
                            width='stretch',
                            help="Removes the selected symbols from live rules. If cancel orders is chosen, linked open orders are canceled first.",
                        )

                    if prune_submitted:
                        if not effective_prune_selection:
                            st.warning("Select at least one symbol before pruning live rules.")
                        elif prune_action == "Review in Live Ops":
                            _open_live_ops_for_symbol(symbol=effective_prune_selection[0])
                        elif linked_state_rows and unclear_symbols:
                            st.error(
                                "Order state is unclear for: "
                                + ", ".join(sorted(set(unclear_symbols)))
                                + ". Review in Live Ops before pruning."
                            )
                        elif prune_action == "Cancel linked orders and prune":
                            if not confirm_cancel_linked:
                                st.error("Confirm the cancel step before continuing.")
                            else:
                                prune_client = private_ctx.get("client")
                                if prune_client is None:
                                    st.error("Private API client is unavailable, so linked orders cannot be canceled.")
                                else:
                                    cancelled_orders: list[dict[str, Any]] = []
                                    ambiguous_symbols: list[str] = []
                                    error_lines: list[str] = []
                                    selection_set = set(effective_prune_selection)
                                    for order in open_execution_orders:
                                        if str(order.get("symbol")) not in selection_set:
                                            continue
                                        try:
                                            canceled_record, events = cancel_live_order(
                                                client=prune_client,
                                                order_record=order,
                                                occurred_at=now_text(),
                                            )
                                            persist_execution_order_update(
                                                int(order["id"]),
                                                canceled_record,
                                                events,
                                            )
                                            cancelled_orders.append(canceled_record)
                                            if str(canceled_record.get("state")) not in {"canceled", "filled"}:
                                                ambiguous_symbols.append(str(order.get("symbol")))
                                        except Exception as e:
                                            ambiguous_symbols.append(str(order.get("symbol")))
                                            error_lines.append(
                                                f"{order.get('symbol')}: cancel failed ({e})"
                                            )

                                    if ambiguous_symbols:
                                        st.error(
                                            "Cancel result was not clear for: "
                                            + ", ".join(sorted(set(ambiguous_symbols)))
                                            + ". Review in Live Ops before pruning."
                                        )
                                        if error_lines:
                                            st.warning("; ".join(error_lines[:3]))
                                    elif cancelled_orders:
                                        updated = _build_pruned_live_rules_config(
                                            config=config,
                                            symbols_to_prune=list(effective_prune_selection),
                                            remove_from_watchlist=bool(remove_from_watchlist),
                                        )
                                        if save_config_with_feedback(
                                            config,
                                            updated,
                                            f"Canceled linked orders and pruned {len(effective_prune_selection)} symbol(s)",
                                        ):
                                            insert_runtime_event(
                                                created_at=now_text(),
                                                event_type="strategy_tuning",
                                                severity="info",
                                                message=f"Canceled linked orders and pruned {len(effective_prune_selection)} symbol(s)",
                                                details={
                                                    "action": "cancel_linked_orders_and_prune",
                                                    "symbols": list(effective_prune_selection),
                                                    "remove_from_watchlist": bool(remove_from_watchlist),
                                                },
                                            )
                                            _cached_strategy_tuning_history.clear()
                                            st.rerun()
                                    else:
                                        st.error("No linked orders were canceled, so pruning was not applied.")
                        else:
                            updated = _build_pruned_live_rules_config(
                                config=config,
                                symbols_to_prune=list(effective_prune_selection),
                                remove_from_watchlist=bool(remove_from_watchlist),
                            )
                            if save_config_with_feedback(
                                config,
                                updated,
                                f"Pruned {len(effective_prune_selection)} symbol(s) from live rules",
                            ):
                                insert_runtime_event(
                                    created_at=now_text(),
                                    event_type="strategy_tuning",
                                    severity="info",
                                    message=f"Pruned {len(effective_prune_selection)} symbol(s) from live rules",
                                    details={
                                        "action": "prune_live_rules",
                                        "symbols": list(effective_prune_selection),
                                        "remove_from_watchlist": bool(remove_from_watchlist),
                                    },
                                )
                                _cached_strategy_tuning_history.clear()
                                st.rerun()
            with tuning_tabs[1]:
                if tuning_rows:
                    st.dataframe(tuning_rows, width='stretch', hide_index=True)
                else:
                    st.caption("No live rules configured yet. Promote ranked symbols or add rules first.")

        with tuning_right:
            tuning_focus_options = [row["symbol"] for row in tuning_rows]
            if tuning_focus_options:
                default_focus_symbol = (
                    st.session_state.pop("strategy_tuning_focus_autorun", None)
                    or (
                        prune_rows[0]["symbol"]
                        if prune_rows
                        else review_rows[0]["symbol"]
                        if review_rows
                        else monitor_rows[0]["symbol"]
                        if monitor_rows
                        else tuning_focus_options[0]
                    )
                )
                if default_focus_symbol not in tuning_focus_options:
                    default_focus_symbol = tuning_focus_options[0]
                _sync_select_state(
                    key="strategy_tuning_focus_symbol",
                    options=tuning_focus_options,
                    default=str(default_focus_symbol),
                )
                focus_symbol = st.selectbox(
                    "Rule Focus",
                    tuning_focus_options,
                    key="strategy_tuning_focus_symbol",
                )
                focus_row = next(row for row in tuning_rows if row["symbol"] == focus_symbol)
                st.markdown("**── Review ──**")
                _render_symbol_operational_state(
                    symbol=str(focus_row["symbol"]),
                    config=config,
                    private_ctx=private_ctx,
                    latest_prices=latest_prices,
                    runtime=runtime,
                    open_execution_orders=open_execution_orders,
                    title="Symbol Operational State",
                    kicker="State",
                )
                st.markdown(
                    badge(
                        f"{focus_row['symbol']} -> {focus_row['recommendation']} | {focus_row['confidence']}",
                        "good" if focus_row["recommendation"] == "KEEP" else "warn" if focus_row["recommendation"] in {"MONITOR", "REVIEW"} else "bad",
                    ),
                    unsafe_allow_html=True,
                )
                st.caption(f"Auto-entry gate: {focus_row['auto_entry_pass']} | {focus_row['gate_reason']}")
                st.caption(
                    f"Replay: trades={focus_row['replay_trades']} pnl={focus_row['replay_pnl_thb']:,.2f} THB win_rate={focus_row['replay_win_rate']:.2f}% hold={focus_row['replay_avg_hold_min']:.1f} min"
                )
                st.caption(
                    f"Fees: total={focus_row.get('replay_total_fee_thb', 0.0):,.2f} THB avg={focus_row.get('replay_avg_fee_thb', 0.0):,.2f} fee_drag={focus_row.get('replay_fee_drag_percent', 0.0):.2f}% -> {focus_row.get('fee_guardrail', 'n/a')}"
                )
                st.caption(
                    f"Market: last={focus_row['last_close']:,.8f} | {focus_row['market_context']} | entry gap {focus_row['entry_gap_percent']:+.2f}% | target gap {focus_row['target_gap_percent']:+.2f}%"
                )
                st.caption(
                    f"Rule: buy_below={focus_row['buy_below']:,.8f} sell_above={focus_row['sell_above']:,.8f} stop_ref={focus_row['stop_reference']:,.8f} ({focus_row['stop_gap_percent']:+.2f}%) take={focus_row['take_profit_percent']:.2f}%"
                )
                _render_live_rule_price_overlay(
                    symbol=str(focus_row["symbol"]),
                    buy_below=float(focus_row["buy_below"]),
                    sell_above=float(focus_row["sell_above"]),
                    latest_prices=latest_prices,
                    quote_fetched_at=quote_fetched_at,
                )
                st.caption(f"Tuning note: {focus_row['tuning_note']}")
                st.caption(f"Confidence: {focus_row['confidence_note']}")
                st.caption(f"Fee guardrail: {focus_row.get('fee_guardrail_note', 'n/a')}")
                st.markdown("**── Explore further ──**")
                nav_left, nav_right = st.columns(2)
                with nav_left:
                    if st.button(
                        "Open Live Ops",
                        key=f"tuning_open_live_ops_{focus_row['symbol']}",
                        width='stretch',
                        help="Navigates away from Strategy to the Live Ops page for this symbol.",
                    ):
                        _open_live_ops_for_symbol(symbol=str(focus_row["symbol"]))
                with nav_right:
                    if st.button(
                        "Open Compare",
                        key=f"tuning_open_compare_{focus_row['symbol']}",
                        width='stretch',
                        help="Switches to the Compare Lab tab and pre-fills this symbol. Nothing changes until you click Apply.",
                    ):
                        _queue_strategy_workspace(
                            workspace="Compare",
                            symbol=str(focus_row["symbol"]),
                        )
                        st.query_params["page"] = "Strategy"
                        st.rerun()
                focus_next_step = (
                    "Next step: keep this symbol live and monitor Latest Auto Entry Review for execution quality."
                    if focus_row["recommendation"] == "KEEP"
                    else "Next step: run Strategy Compare Lab on this symbol before widening automation."
                    if focus_row["recommendation"] == "REVIEW"
                    else "Next step: gather more samples before changing the rule."
                    if focus_row["recommendation"] == "MONITOR"
                    else "Next step: prune this symbol from live rules unless you have a strong reason to keep it live."
                )
                st.info(focus_next_step)
            else:
                st.caption("No tuning focus available yet.")

    if should_show_compare:
        render_section_intro(
            "Compare Lab",
            "Run parameter variants for one live symbol and compare them against the current baseline. Nothing changes until you click Apply to Live Config.",
            "Compare Lab",
        )
        _decision_context = st.session_state.pop("strategy_decision_context", None)

        compare_symbol_options = configured_symbols or symbols
        compare_source_options = ["candles", "snapshots"]
        compare_symbol_key = "strategy_compare_symbol"
        compare_source_key = "strategy_compare_source"
        compare_resolution_key = "strategy_compare_resolution"
        compare_days_key = "strategy_compare_days"
        compare_symbol_input_key = f"{compare_symbol_key}__input"
        compare_source_input_key = f"{compare_source_key}__input"
        compare_resolution_input_key = f"{compare_resolution_key}__input"
        compare_days_input_key = f"{compare_days_key}__input"
        compare_autorun = st.session_state.pop("strategy_compare_autorun", None)

        if compare_autorun:
            _clear_strategy_compare_state()
            autorun_symbol = str(compare_autorun.get("symbol", compare_symbol_options[0]))
            autorun_source = str(compare_autorun.get("source", "candles"))
            autorun_resolution = str(compare_autorun.get("resolution", ranking_resolution))
            autorun_days = int(compare_autorun.get("days", ranking_days) or ranking_days)
            if autorun_symbol in compare_symbol_options:
                st.session_state[compare_symbol_key] = autorun_symbol
                st.session_state[compare_symbol_input_key] = autorun_symbol
            if autorun_source in compare_source_options:
                st.session_state[compare_source_key] = autorun_source
                st.session_state[compare_source_input_key] = autorun_source
            if autorun_resolution in rank_resolution_options:
                st.session_state[compare_resolution_key] = autorun_resolution
                st.session_state[compare_resolution_input_key] = autorun_resolution
            st.session_state[compare_days_key] = min(max(int(autorun_days), 1), 90)
            st.session_state[compare_days_input_key] = st.session_state[compare_days_key]
            prefill_line = f"Pre-filled: {autorun_symbol}  ·  {autorun_source}  ·  resolution {autorun_resolution}  ·  {autorun_days}d lookback. You can change any setting below before running."
            if _decision_context:
                render_callout(
                    "From Decisions",
                    f"{_decision_context}<br><br>{prefill_line}",
                    "info",
                )
            else:
                render_callout("Pre-filled from Live Rules", prefill_line, "info")
        elif _decision_context:
            render_callout("From Decisions", _decision_context, "info")

        current_symbol = st.session_state.get(compare_symbol_key)
        current_input = st.session_state.get(compare_symbol_input_key)
        if current_symbol and current_symbol not in compare_symbol_options:
            st.session_state.pop(compare_symbol_key, None)
        if current_input and current_input not in compare_symbol_options:
            st.session_state.pop(compare_symbol_input_key, None)

        compare_default_symbol = str(st.session_state.get(compare_symbol_key, compare_symbol_options[0]))
        if compare_default_symbol not in compare_symbol_options:
            compare_default_symbol = compare_symbol_options[0]
        compare_default_source = str(st.session_state.get(compare_source_key, "candles"))
        if compare_default_source not in compare_source_options:
            compare_default_source = "candles"
        compare_default_resolution = str(st.session_state.get(compare_resolution_key, ranking_resolution))
        if compare_default_resolution not in rank_resolution_options:
            compare_default_resolution = ranking_resolution
        compare_default_days = int(st.session_state.get(compare_days_key, ranking_days))
        compare_default_days = min(max(compare_default_days, 1), 90)

        _sync_select_state(
            key=compare_symbol_input_key,
            options=compare_symbol_options,
            default=compare_default_symbol,
        )
        _sync_select_state(
            key=compare_source_input_key,
            options=compare_source_options,
            default=compare_default_source,
        )
        _sync_select_state(
            key=compare_resolution_input_key,
            options=rank_resolution_options,
            default=compare_default_resolution,
        )
        current_compare_days_raw = int(st.session_state.get(compare_days_input_key, compare_default_days))
        current_compare_days = min(max(current_compare_days_raw, 1), 90)
        if compare_days_input_key not in st.session_state or current_compare_days_raw != current_compare_days:
            st.session_state[compare_days_input_key] = current_compare_days

        with st.form("strategy_compare_form"):
            compare_meta_left, compare_meta_right = st.columns(2)
            with compare_meta_left:
                compare_symbol = st.selectbox(
                    "Compare Symbol",
                    compare_symbol_options,
                    key=compare_symbol_input_key,
                )
                compare_source = st.selectbox(
                    "Compare Source",
                    compare_source_options,
                    key=compare_source_input_key,
                )
            with compare_meta_right:
                compare_resolution = st.selectbox(
                    "Compare Resolution",
                    rank_resolution_options,
                    key=compare_resolution_input_key,
                    help="Used when Compare Source = candles.",
                )
                compare_days = st.number_input(
                    "Compare Lookback Days",
                    min_value=1,
                    max_value=90,
                    key=compare_days_input_key,
                    step=1,
                )
            run_compare = st.form_submit_button("Run Compare", type="primary", width='stretch')

        compare_selection_freshness = _build_compare_data_freshness(
            source=str(compare_source),
            resolution=str(compare_resolution),
            last_timestamp=_load_compare_data_last_timestamp(
                symbol=str(compare_symbol),
                source=str(compare_source),
                resolution=str(compare_resolution),
            ),
        )
        _render_compare_data_freshness(freshness=compare_selection_freshness)

        if configured_symbols:
            should_run_compare = run_compare or "strategy_compare_payload" not in st.session_state or bool(compare_autorun)
            if should_run_compare:
                compare_symbol = str(compare_symbol)
                compare_source = str(compare_source)
                compare_resolution = str(compare_resolution)
                compare_days = int(compare_days)
                st.session_state[compare_symbol_key] = compare_symbol
                st.session_state[compare_source_key] = compare_source
                st.session_state[compare_resolution_key] = compare_resolution
                st.session_state[compare_days_key] = compare_days
                compare_base_rule = build_rule_seed(config, compare_symbol)
                compare_variants = build_rule_compare_variants(base_rule=compare_base_rule)
                compare_rows = run_strategy_compare_rows(
                    symbol=compare_symbol,
                    replay_source=compare_source,
                    replay_resolution=str(compare_resolution),
                    lookback_days=int(compare_days),
                    fee_rate=float(config["fee_rate"]),
                    cooldown_seconds=int(config["cooldown_seconds"]),
                    variants=compare_variants,
                    cache_token=_strategy_compare_cache_token(
                        session_state=st.session_state,
                        symbol=compare_symbol,
                        source=compare_source,
                        resolution=str(compare_resolution),
                    ),
                )
                compare_rows = annotate_strategy_compare_rows(compare_rows)
                st.session_state["strategy_compare_payload"] = {
                    "symbol": compare_symbol,
                    "source": compare_source,
                    "resolution": str(compare_resolution),
                    "days": int(compare_days),
                    "last_timestamp": _compare_payload_last_timestamp(compare_rows),
                    "rows": compare_rows,
                    "variant_rules": {row["variant"]: dict(row["rule"]) for row in compare_rows},
                }

            compare_payload = st.session_state.get("strategy_compare_payload")
            if compare_payload:
                compare_rows = list(compare_payload.get("rows") or [])
                compare_payload_freshness = _build_compare_data_freshness(
                    source=str(compare_payload.get("source") or ""),
                    resolution=str(compare_payload.get("resolution") or ""),
                    last_timestamp=str(compare_payload.get("last_timestamp") or "") or None,
                )
                profitable_variants = sum(1 for row in compare_rows if float(row.get("total_pnl_thb", 0.0) or 0.0) > 0)
                best_variant = next((row for row in compare_rows if str(row.get("decision")) in {"Clearly better", "Marginally better"}), compare_rows[0] if compare_rows else None)
                compare_cards = st.columns(4)
                with compare_cards[0]:
                    render_metric_card("Compared Variants", str(len(compare_rows)), f"Symbol {compare_payload.get('symbol', 'n/a')}")
                with compare_cards[1]:
                    render_metric_card("Profitable Variants", str(profitable_variants), f"Source {compare_payload.get('source', 'n/a')}")
                with compare_cards[2]:
                    render_metric_card("Best Candidate", str(best_variant.get('variant', 'n/a')) if best_variant else "n/a", str(best_variant.get('decision', 'n/a')) if best_variant else "n/a")
                with compare_cards[3]:
                    render_metric_card("Best Fee Drag", f"{float(best_variant.get('fee_drag_percent', 0.0) if best_variant else 0.0):.2f}%", f"Lookback {compare_payload.get('days', 'n/a')} day(s)")

                st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
                compare_left, compare_right = st.columns([1.1, 0.9])
                with compare_left:
                    st.dataframe(
                        [
                            {key: value for key, value in row.items() if key not in {"rule", "decision_rank"}}
                            for row in compare_rows
                        ],
                        width='stretch',
                        hide_index=True,
                    )
                with compare_right:
                    focus_variant_options = [row["variant"] for row in compare_rows]
                    compare_scope = _strategy_compare_scope_key(
                        symbol=str(compare_payload.get("symbol", "")),
                        source=str(compare_payload.get("source", "")),
                        resolution=str(compare_payload.get("resolution", "")),
                        days=int(compare_payload.get("days", 0) or 0),
                    )
                    persisted_selection = _latest_strategy_compare_selection_map().get(compare_scope, {})
                    latest_applied_variant = _latest_strategy_compare_applied_map().get(compare_scope, {})
                    preferred_variant = (
                        str(persisted_selection.get("focus_variant"))
                        if str(persisted_selection.get("focus_variant") or "") in focus_variant_options
                        else str(latest_applied_variant.get("variant"))
                        if str(latest_applied_variant.get("variant") or "") in focus_variant_options
                        else str(best_variant.get("variant"))
                        if best_variant and str(best_variant.get("variant")) in focus_variant_options
                        else focus_variant_options[0]
                    )
                    focus_variant_key = f"strategy_compare_focus_variant::{compare_scope}"
                    apply_variant_key = f"strategy_compare_apply_variant::{compare_scope}"
                    _sync_select_state(
                        key=focus_variant_key,
                        options=focus_variant_options,
                        default=preferred_variant,
                    )
                    selected_variant = st.selectbox(
                        "Variant Focus",
                        focus_variant_options,
                        key=focus_variant_key,
                    )
                    focus_variant_persist_key = f"{focus_variant_key}__persisted_value"
                    if focus_variant_persist_key not in st.session_state:
                        st.session_state[focus_variant_persist_key] = selected_variant
                    elif str(st.session_state.get(focus_variant_persist_key) or "") != str(selected_variant):
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type="strategy_compare_selection",
                            severity="info",
                            message=f"Selected compare variant {selected_variant} for {compare_payload['symbol']}",
                            details={
                                "symbol": str(compare_payload.get("symbol", "")),
                                "source": str(compare_payload.get("source", "")),
                                "resolution": str(compare_payload.get("resolution", "")),
                                "days": int(compare_payload.get("days", 0) or 0),
                                "focus_variant": str(selected_variant),
                            },
                        )
                        st.session_state[focus_variant_persist_key] = selected_variant
                        _cached_strategy_compare_selection_history.clear()
                    focus_variant_row = next(row for row in compare_rows if row["variant"] == selected_variant)
                    decision_tone = "good" if str(focus_variant_row.get("decision")) in {"Clearly better", "Marginally better"} else "warn" if str(focus_variant_row.get("decision")) in {"Tied with baseline", "Needs more samples", "Current baseline"} else "bad"
                    _render_symbol_operational_state(
                        symbol=str(compare_payload.get("symbol", "")),
                        config=config,
                        private_ctx=private_ctx,
                        latest_prices=latest_prices,
                        runtime=runtime,
                        open_execution_orders=open_execution_orders,
                        title="Symbol Operational State",
                        kicker="State",
                    )
                    st.markdown(
                        badge(
                            f"{focus_variant_row['variant']} | {focus_variant_row['decision']}",
                            decision_tone,
                        ),
                        unsafe_allow_html=True,
                    )
                    _render_compare_data_freshness(freshness=compare_payload_freshness)
                    st.caption(
                        f"Rule: buy_below={focus_variant_row['buy_below']:,.8f} sell_above={focus_variant_row['sell_above']:,.8f} stop={focus_variant_row['stop_loss_percent']:.2f}% take={focus_variant_row['take_profit_percent']:.2f}%"
                    )
                    current_live_rule = dict(
                        config["rules"].get(str(compare_payload.get("symbol", ""))) or {}
                    )
                    if current_live_rule:
                        _render_live_rule_price_overlay(
                            symbol=str(compare_payload.get("symbol", "")),
                            buy_below=float(current_live_rule.get("buy_below", 0.0) or 0.0),
                            sell_above=float(current_live_rule.get("sell_above", 0.0) or 0.0),
                            latest_prices=latest_prices,
                            quote_fetched_at=quote_fetched_at,
                        )
                    st.caption(
                        f"Replay: trades={focus_variant_row['trades']} win_rate={focus_variant_row['win_rate_percent']:.2f}% hold={focus_variant_row['avg_hold_minutes']:.1f} min open_position={focus_variant_row['open_position']}"
                    )
                    st.caption(
                        f"Fees: total={float(focus_variant_row.get('total_fee_thb', 0.0) or 0.0):,.2f} THB | fee drag={float(focus_variant_row.get('fee_drag_percent', 0.0) or 0.0):.2f}% | {focus_variant_row.get('fee_guardrail', 'n/a')}"
                    )
                    st.caption(f"Fee note: {focus_variant_row.get('fee_guardrail_note', 'n/a')}")
                    st.caption(f"Decision: {focus_variant_row['decision_reason']}")
                    st.caption(f"Variant note: {focus_variant_row['note']}")
                    current_variant_rule = dict(compare_payload.get("variant_rules", {}).get("CURRENT") or {})
                    if latest_applied_variant:
                        if dict(latest_applied_variant.get("rule") or {}) == current_variant_rule:
                            st.caption(
                                f"Current live rule matches last applied variant {latest_applied_variant.get('variant', 'n/a')} ({latest_applied_variant.get('created_at', 'n/a')})."
                            )
                        else:
                            st.caption(
                                f"Last applied variant was {latest_applied_variant.get('variant', 'n/a')} ({latest_applied_variant.get('created_at', 'n/a')}), but the current live rule has changed since then."
                            )

                    nav_left, nav_right = st.columns(2)
                    with nav_left:
                        if st.button(
                            "Open Live Ops",
                            key=f"compare_open_live_ops_{compare_payload['symbol']}",
                            width='stretch',
                            help="Navigates away from Strategy to the Live Ops page for this symbol.",
                        ):
                            _open_live_ops_for_symbol(symbol=str(compare_payload["symbol"]))
                    with nav_right:
                        if st.button(
                            "Open Live Tuning",
                            key=f"compare_open_live_tuning_{compare_payload['symbol']}",
                            width='stretch',
                            help="Switches to the Live Rules tab and focuses this symbol. No config change.",
                        ):
                            _queue_strategy_workspace(
                                workspace="Live Tuning",
                                symbol=str(compare_payload["symbol"]),
                            )
                            st.query_params["page"] = "Strategy"
                            st.rerun()

                    _sync_select_state(
                        key=apply_variant_key,
                        options=focus_variant_options,
                        default=selected_variant,
                    )
                    with st.form("strategy_apply_compared_variant_form"):
                        apply_variant = st.selectbox(
                            "Apply Variant To Live Rule",
                            focus_variant_options,
                            key=apply_variant_key,
                        )
                        if str(compare_payload_freshness.get("status") or "") in {"Stale", "Missing"}:
                            st.warning(
                                "Compare result is using stale or missing source data. Sync candles and rerun Compare before applying a variant."
                            )
                        apply_submitted = st.form_submit_button(
                            "Apply to Live Config",
                            type="primary",
                            width='stretch',
                            help="Writes the selected variant's parameters to config as the new live rule. This mutates config and cannot be undone without re-running Compare.",
                        )
                    if apply_submitted:
                        chosen_rule = dict(compare_payload.get("variant_rules", {}).get(apply_variant) or {})
                        if not chosen_rule:
                            st.warning("Could not find the selected variant rule to apply. Run Compare again.")
                        else:
                            updated = dict(config)
                            updated_rules = dict(config["rules"])
                            updated_rules[str(compare_payload["symbol"])] = chosen_rule
                            updated["rules"] = updated_rules
                            if save_config_with_feedback(
                                config,
                                updated,
                                f"Applied compared variant {apply_variant} to {compare_payload['symbol']}",
                            ):
                                insert_runtime_event(
                                    created_at=now_text(),
                                    event_type="strategy_tuning",
                                    severity="info",
                                    message=f"Applied variant {apply_variant} to {compare_payload['symbol']}",
                                    details={
                                        "action": "apply_compared_variant",
                                        "symbol": str(compare_payload["symbol"]),
                                        "variant": str(apply_variant),
                                        "source": str(compare_payload.get("source", compare_source)),
                                        "resolution": str(compare_payload.get("resolution", compare_resolution)),
                                        "days": int(compare_payload.get("days", compare_days)),
                                        "rule": chosen_rule,
                                    },
                                )
                                _cached_strategy_tuning_history.clear()
                                st.session_state["strategy_tuning_focus_autorun"] = str(compare_payload["symbol"])
                                st.session_state[focus_variant_persist_key] = str(apply_variant)
                                _cached_strategy_compare_selection_history.clear()
                                st.session_state.pop("strategy_compare_payload", None)
                                st.session_state["strategy_compare_autorun"] = {
                                    "symbol": str(compare_payload["symbol"]),
                                    "source": str(compare_payload.get("source", compare_source)),
                                    "resolution": str(compare_payload.get("resolution", compare_resolution)),
                                    "days": int(compare_payload.get("days", compare_days)),
                                }
                                st.rerun()
            else:
                st.caption("Run Compare to evaluate multiple variants for one live-rule symbol.")
        else:
            st.caption("No live rules configured yet, so compare mode is unavailable.")

    if should_show_tuning:
        auto_entry_report = _build_auto_entry_review_report(limit=40)
        with st.expander("Auto Entry Review Report", expanded=False):
            review_events = list(auto_entry_report.get("events") or [])
            report_context = dict(auto_entry_report.get("latest_context") or {})
            report_cards = st.columns(4)
            with report_cards[0]:
                render_metric_card("Review Events", str(len(review_events)), "Recent auto-entry reviews")
            with report_cards[1]:
                render_metric_card("Top Rejection", str((auto_entry_report.get("rejection_summary") or [{"reason": "n/a"}])[0].get("reason", "n/a")) if auto_entry_report.get("rejection_summary") else "n/a", f"Count {int((auto_entry_report.get('rejection_summary') or [{'count': 0}])[0].get('count', 0))}")
            with report_cards[2]:
                render_metric_card("Most Seen Candidate", str((auto_entry_report.get("symbol_candidate_summary") or [{"symbol": "n/a"}])[0].get("symbol", "n/a")) if auto_entry_report.get("symbol_candidate_summary") else "n/a", f"Hits {int((auto_entry_report.get('symbol_candidate_summary') or [{'candidate_hits': 0}])[0].get('candidate_hits', 0))}")
            with report_cards[3]:
                render_metric_card("Current Gate", f"min_score {float(report_context.get('min_score', 0.0)):.1f}", ", ".join(report_context.get("allowed_biases", [])) or "n/a")

            report_left, report_right = st.columns([1.0, 1.0])
            with report_left:
                st.markdown("#### Rejection Summary")
                if auto_entry_report.get("rejection_summary"):
                    st.dataframe(auto_entry_report["rejection_summary"], width='stretch', hide_index=True)
                else:
                    st.caption("No rejection reasons recorded yet.")
                st.markdown("#### Candidate Frequency")
                if auto_entry_report.get("symbol_candidate_summary"):
                    st.dataframe(auto_entry_report["symbol_candidate_summary"], width='stretch', hide_index=True)
                else:
                    st.caption("No candidates recorded yet.")
            with report_right:
                st.markdown("#### Top Candidate Snapshots")
                if auto_entry_report.get("top_candidates"):
                    st.dataframe(auto_entry_report["top_candidates"], width='stretch', hide_index=True)
                else:
                    st.caption("No candidate snapshots recorded yet.")
                st.markdown("#### Symbols Rejected Most Often")
                if auto_entry_report.get("symbol_reject_summary"):
                    st.dataframe(auto_entry_report["symbol_reject_summary"], width='stretch', hide_index=True)
                else:
                    st.caption("No repeated rejects recorded yet.")

        tuning_history = _cached_strategy_tuning_history(limit=8)
        with st.expander("Recent Tuning Actions", expanded=False):
            if tuning_history:
                st.dataframe(
                    [
                        {
                            "created_at": row["created_at"],
                            "message": row["message"],
                            "action": str((row.get("details") or {}).get("action", "n/a")),
                        }
                        for row in tuning_history
                    ],
                    width='stretch',
                    hide_index=True,
                )
            else:
                st.caption("No tuning actions recorded yet. Apply a variant or prune live rules to build history.")

    if should_show_replay:
        render_section_intro(
            "Replay Lab",
            "Manual deep-dive for a single symbol with your own parameters. Read-only — no config changes from here.",
            "Replay Lab",
        )

        default_symbol = str(st.session_state.get("strategy_replay_symbol", symbols[0]))
        if default_symbol not in symbols:
            default_symbol = symbols[0]
        replay_source_options = ["candles", "snapshots"]
        default_replay_source = str(st.session_state.get("strategy_replay_source", "candles"))
        if default_replay_source not in replay_source_options:
            default_replay_source = "candles"
        default_replay_resolution = str(st.session_state.get("strategy_replay_resolution", ranking_resolution))
        if default_replay_resolution not in rank_resolution_options:
            default_replay_resolution = ranking_resolution
        default_replay_days = int(st.session_state.get("strategy_replay_days", min(14, max(14, coverage_days))))

        replay_meta_left, replay_meta_right = st.columns(2)
        with replay_meta_left:
            replay_symbol = st.selectbox(
                "Replay Symbol",
                symbols,
                index=symbols.index(default_symbol),
                key="strategy_replay_symbol",
            )
            replay_source = st.selectbox(
                "Replay Source",
                replay_source_options,
                index=replay_source_options.index(default_replay_source),
                key="strategy_replay_source",
                help="Candles use TradingView history stored in SQLite. Snapshots use the older market_snapshots feed.",
            )
        with replay_meta_right:
            replay_resolution = st.selectbox(
                "Replay Candle Resolution",
                rank_resolution_options,
                index=rank_resolution_options.index(default_replay_resolution),
                key="strategy_replay_resolution",
                help="Used only when Replay Source = candles.",
            )
            lookback_days = st.number_input(
                "Replay Lookback Days",
                min_value=1,
                max_value=90,
                value=default_replay_days,
                step=1,
                key="strategy_replay_days",
            )

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
        replay_market_price = ranking_last_close_by_symbol.get(replay_symbol)
        if replay_market_price is None and replay_symbol not in config["rules"] and replay_symbol:
            replay_price_ranking = _cached_coin_ranking(
                symbols=(str(replay_symbol),),
                resolution=str(replay_resolution),
                lookback_days=int(lookback_days),
            )
            if replay_price_ranking.get("rows"):
                replay_market_price = float(replay_price_ranking["rows"][0].get("last_close", 0.0) or 0.0)

        active_rule = build_rule_seed(
            config,
            replay_symbol,
            market_price=replay_market_price,
        )
        replay_snapshot_cards = st.columns(4)
        with replay_snapshot_cards[0]:
            render_metric_card("Rule Buy Below", f"{float(active_rule['buy_below']):,.8f}", replay_symbol)
        with replay_snapshot_cards[1]:
            render_metric_card("Rule Sell Above", f"{float(active_rule['sell_above']):,.8f}", f"Budget {float(active_rule['budget_thb']):,.2f} THB")
        with replay_snapshot_cards[2]:
            render_metric_card("Stop Loss", f"{float(active_rule['stop_loss_percent']):.2f}%", f"Take profit {float(active_rule['take_profit_percent']):.2f}%")
        with replay_snapshot_cards[3]:
            render_metric_card("Max Trades / Day", str(int(active_rule['max_trades_per_day'])), f"Cooldown {int(config['cooldown_seconds'])} sec")

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
        with st.form("strategy_replay_form"):
            form_left, form_right = st.columns(2)
            with form_left:
                buy_below = st.number_input("Buy Below", min_value=0.0, value=float(active_rule["buy_below"]), format="%.8f", key=f"strategy_replay_buy_below_{replay_symbol}")
                sell_above = st.number_input("Sell Above", min_value=0.0, value=float(active_rule["sell_above"]), format="%.8f", key=f"strategy_replay_sell_above_{replay_symbol}")
                budget_thb = st.number_input("Budget THB", min_value=1.0, value=float(active_rule["budget_thb"]), step=10.0, key=f"strategy_replay_budget_{replay_symbol}")
                max_trades_per_day = st.number_input(
                    "Max Trades / Day",
                    min_value=1,
                    value=int(active_rule["max_trades_per_day"]),
                    step=1,
                    key=f"strategy_replay_max_trades_{replay_symbol}",
                )
            with form_right:
                stop_loss_percent = st.number_input(
                    "Stop Loss %",
                    min_value=0.01,
                    value=float(active_rule["stop_loss_percent"]),
                    format="%.2f",
                    key=f"strategy_replay_stop_loss_{replay_symbol}",
                )
                take_profit_percent = st.number_input(
                    "Take Profit %",
                    min_value=0.01,
                    value=float(active_rule["take_profit_percent"]),
                    format="%.2f",
                    key=f"strategy_replay_take_profit_{replay_symbol}",
                )
                cooldown_seconds = st.number_input(
                    "Cooldown Seconds",
                    min_value=0,
                    value=int(config["cooldown_seconds"]),
                    step=1,
                    key=f"strategy_replay_cooldown_{replay_symbol}",
                )
                fee_rate = st.number_input(
                    "Fee Rate",
                    min_value=0.0,
                    max_value=0.9999,
                    value=float(config["fee_rate"]),
                    format="%.6f",
                    key=f"strategy_replay_fee_{replay_symbol}",
                )
            run_replay = st.form_submit_button("Run Replay", type="primary", width='stretch')

        replay_request = {
            "symbol": replay_symbol,
            "source": replay_source,
            "resolution": str(replay_resolution),
            "days": int(lookback_days),
            "buy_below": float(buy_below),
            "sell_above": float(sell_above),
            "budget_thb": float(budget_thb),
            "stop_loss_percent": float(stop_loss_percent),
            "take_profit_percent": float(take_profit_percent),
            "max_trades_per_day": int(max_trades_per_day),
            "cooldown_seconds": int(cooldown_seconds),
            "fee_rate": float(fee_rate),
        }
        prior_replay_request = st.session_state.get("strategy_replay_request")
        if run_replay or "strategy_replay_result" not in st.session_state or prior_replay_request != replay_request:
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
            st.session_state["strategy_replay_request"] = replay_request

        replay = st.session_state.get("strategy_replay_result")
        if replay:
            metrics = replay["metrics"]
            replay_card1, replay_card2, replay_card3, replay_card4 = st.columns(4)
            with replay_card1:
                render_metric_card("Replay Trades", str(metrics["trades"]), f"{replay.get('source', 'replay')} rows {replay.get('candles', replay.get('snapshots', replay.get('bars', 0)))}")
            with replay_card2:
                render_metric_card("Replay PnL", f"{metrics['total_pnl_thb']:,.2f} THB", f"Win rate {metrics['win_rate_percent']:.2f}%")
            with replay_card3:
                render_metric_card("Replay Avg/Trade", f"{metrics['avg_pnl_thb']:,.2f}", f"Profit factor {metrics['profit_factor']:.2f} | Fee drag {metrics.get('fee_drag_percent', 0.0):.2f}%")
            with replay_card4:
                render_metric_card("Replay Fees", f"{metrics.get('total_fee_thb', 0.0):,.2f} THB", f"Avg fee {metrics.get('avg_fee_thb', 0.0):,.2f} | Hold {metrics['avg_hold_minutes']:.1f} min")

            replay_fee_guardrail, replay_fee_note, _ = evaluate_fee_guardrail(
                trades=int(metrics.get('trades', 0) or 0),
                total_pnl_thb=float(metrics.get('total_pnl_thb', 0.0) or 0.0),
                total_fee_thb=float(metrics.get('total_fee_thb', 0.0) or 0.0),
                avg_pnl_thb=float(metrics.get('avg_pnl_thb', 0.0) or 0.0),
                avg_fee_thb=float(metrics.get('avg_fee_thb', 0.0) or 0.0),
                fee_drag_percent=float(metrics.get('fee_drag_percent', 0.0) or 0.0),
            )
            if replay_fee_guardrail in {"FEE_HEAVY", "THIN_EDGE", "LOSS_AFTER_FEES"}:
                st.warning(f"Replay fee guardrail: {replay_fee_guardrail} | {replay_fee_note}")
            else:
                st.caption(f"Replay fee guardrail: {replay_fee_guardrail} | {replay_fee_note}")

            replay_left, replay_right = st.columns([1.05, 0.95])
            with replay_left:
                st.markdown('<div class="panel-title">Replay Trades</div>', unsafe_allow_html=True)
                if replay["trades"]:
                    st.dataframe(replay["trades"], width='stretch', hide_index=True)
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
            st.dataframe(coverage_rows, width='stretch', hide_index=True)
        else:
            st.caption("No market snapshot coverage was found in SQLite yet.")


def render_reports_page(*, today: str, config: dict[str, Any]) -> None:
    render_section_intro(
        "Reports",
        "Start with daily outcome and fee drag, then use symbol and execution tables for the why behind the numbers.",
        "Reporting",
    )
    symbols = ["ALL"] + sorted(config["rules"].keys())
    report_filter_col, report_window_col = st.columns([0.6, 0.4])
    with report_filter_col:
        selected_symbol = st.selectbox("Report Filter", symbols, index=0)
    with report_window_col:
        daily_window_days = int(
            st.select_slider(
                "Daily Summary Window",
                options=[7, 14, 30, 60],
                value=14,
                key="reports_daily_window",
            )
        )

    selected_symbol_key = "" if selected_symbol == "ALL" else selected_symbol
    reports_payload = _cached_reports_page_payload(
        today=today,
        days=daily_window_days,
        symbol_key=selected_symbol_key,
    )
    report = dict(reports_payload.get("report") or {})
    daily_summary_rows = list(reports_payload.get("daily_summary") or [])
    portfolio_daily_metrics = list(reports_payload.get("portfolio_daily_metrics") or [])
    strategy_daily_metrics = list(reports_payload.get("strategy_daily_metrics") or [])
    recent_validation_runs = list(reports_payload.get("recent_validation_runs") or [])
    recent_validation_slices = list(reports_payload.get("recent_validation_slices") or [])
    recent_validation_consistency_checks = list(reports_payload.get("recent_validation_consistency_checks") or [])
    symbol_summary = report["symbol_summary"]
    recent_execution_orders = report["recent_execution_orders"]
    recent_auto_exit_events = report["recent_auto_exit_events"]
    recent_errors = report["recent_errors"]
    recent_trades = report["recent_trades"]
    recent_trade_journal = report.get("recent_trade_journal", [])
    latest_portfolio_metric = portfolio_daily_metrics[0] if portfolio_daily_metrics else {}
    worst_portfolio_drawdown_thb = min(
        (float(row.get("drawdown_thb", 0.0) or 0.0) for row in portfolio_daily_metrics),
        default=0.0,
    )

    total_signals = sum(int(row.get("signals", 0)) for row in symbol_summary)
    total_paper_trades = sum(int(row.get("trades", 0)) for row in symbol_summary)
    total_live_trades = sum(int(row.get("live_closed_trades", 0)) for row in symbol_summary)
    total_combined_trades = total_paper_trades + total_live_trades
    total_paper_pnl = sum(float(row.get("pnl_thb", 0.0)) for row in symbol_summary)
    total_live_pnl = sum(float(row.get("live_realized_pnl_thb", 0.0)) for row in symbol_summary)
    total_combined_pnl = total_paper_pnl + total_live_pnl
    total_paper_fee = sum(float(row.get("paper_fee_thb", 0.0)) for row in symbol_summary)
    total_live_fee = sum(float(row.get("live_fee_thb", 0.0)) for row in symbol_summary)
    total_combined_fee = sum(float(row.get("combined_fee_thb", 0.0)) for row in symbol_summary)

    report_alert_tone = "bad" if total_combined_pnl < 0 else "warn" if total_combined_fee > max(abs(total_combined_pnl) * 0.5, 1.0) else "good"
    report_alert_message = (
        f"PnL {total_combined_pnl:,.2f} THB | Fee {total_combined_fee:,.2f} THB | Signals {total_signals} | Closed trades {total_combined_trades}"
    )
    render_callout("Report Snapshot", report_alert_message, report_alert_tone)

    status_badges = [
        badge(f"Filter {selected_symbol}", "info"),
        badge("PnL NEGATIVE" if total_combined_pnl < 0 else "PnL POSITIVE", "bad" if total_combined_pnl < 0 else "good"),
        badge("Fee HEAVY" if total_combined_fee > max(abs(total_combined_pnl) * 0.5, 1.0) else "Fee OK", "warn" if total_combined_fee > max(abs(total_combined_pnl) * 0.5, 1.0) else "good"),
        badge(f"Errors {len(recent_errors)}", "warn" if recent_errors else "good"),
        badge(f"Journal {len(recent_trade_journal)}", "info" if recent_trade_journal else "good"),
    ]
    st.markdown(f'<div class="status-strip">{" ".join(status_badges)}</div>', unsafe_allow_html=True)

    card1, card2, card3, card4, card5 = st.columns(5)
    with card1:
        render_metric_card("Report Filter", selected_symbol, f"Symbols {len(symbol_summary)} | Window {daily_window_days}d")
    with card2:
        render_metric_card("Signals Today", str(total_signals), f"Closed {total_combined_trades} trade(s)")
    with card3:
        render_metric_card("Closed Trades", str(total_combined_trades), f"Paper {total_paper_trades} | Live {total_live_trades}")
    with card4:
        render_metric_card(
            "PnL Today",
            f"{total_combined_pnl:,.2f} THB",
            f"Paper {total_paper_pnl:,.2f} | Live {total_live_pnl:,.2f}",
        )
    with card5:
        render_metric_card(
            "Fee Today",
            f"{total_combined_fee:,.2f} THB",
            f"Paper {total_paper_fee:,.2f} | Live {total_live_fee:,.2f} | Errors {len(recent_errors)}",
        )

    render_section_intro(
        "Daily PnL Summary",
        f"Daily realized result for the last {daily_window_days} day(s), including paper and live columns in the same table.",
        "Daily",
    )
    if daily_summary_rows:
        st.dataframe(daily_summary_rows, width='stretch', hide_index=True)
    else:
        st.caption("No daily realized PnL rows were found for the selected filter and window.")

    render_section_intro(
        "Portfolio Daily Metrics",
        "Derived metrics rebuilt from paper_trade_logs and filled execution_orders so portfolio reporting stays deterministic and auditable.",
        "Metrics",
    )
    portfolio_metric_cards = st.columns(4)
    with portfolio_metric_cards[0]:
        render_metric_card(
            "Latest Portfolio PnL",
            f"{float(latest_portfolio_metric.get('combined_realized_pnl_thb', 0.0) or 0.0):,.2f} THB",
            f"Date {latest_portfolio_metric.get('report_date', 'n/a')}",
        )
    with portfolio_metric_cards[1]:
        render_metric_card(
            "Cumulative Realized",
            f"{float(latest_portfolio_metric.get('cumulative_realized_pnl_thb', 0.0) or 0.0):,.2f} THB",
            "Across all stored paper and live closed trades",
        )
    with portfolio_metric_cards[2]:
        render_metric_card(
            "Worst Drawdown",
            f"{worst_portfolio_drawdown_thb:,.2f} THB",
            f"Window {daily_window_days}d | Realized curve only",
        )
    with portfolio_metric_cards[3]:
        render_metric_card(
            "Latest Turnover",
            f"{float(latest_portfolio_metric.get('combined_turnover_thb', 0.0) or 0.0):,.2f} THB",
            f"Symbols {int(latest_portfolio_metric.get('symbols_active', 0) or 0)} | Strategies {int(latest_portfolio_metric.get('strategies_active', 0) or 0)}",
        )
    if selected_symbol != "ALL":
        st.caption(
            "Portfolio Daily Metrics stay full-portfolio on purpose. Use Strategy Daily Metrics below for the selected symbol slice."
        )
    if portfolio_daily_metrics:
        st.dataframe(portfolio_daily_metrics, width='stretch', hide_index=True)
    else:
        st.caption("No portfolio daily metric rows have been derived yet.")

    render_section_intro(
        "Strategy Daily Metrics",
        "Per-day rows split by paper_rule_engine and live_execution so you can separate where edge is actually coming from.",
        "Metrics",
    )
    if strategy_daily_metrics:
        st.dataframe(strategy_daily_metrics, width='stretch', hide_index=True)
    else:
        st.caption("No strategy daily metric rows match the current report window and filter.")

    render_section_intro(
        "Validation Runs",
        "Recent walk-forward and time-series CV runs stored in SQLite so validation becomes reviewable instead of a one-off notebook exercise.",
        "Validation",
    )
    latest_validation_run = recent_validation_runs[0] if recent_validation_runs else {}
    validation_cards = st.columns(4)
    with validation_cards[0]:
        render_metric_card(
            "Recent Validation Runs",
            str(len(recent_validation_runs)),
            f"Latest {latest_validation_run.get('validation_type', 'n/a')}",
        )
    with validation_cards[1]:
        render_metric_card(
            "Latest Test PnL",
            f"{float((latest_validation_run.get('summary') or {}).get('test_total_pnl_thb', 0.0) or 0.0):,.2f} THB",
            f"Symbol {latest_validation_run.get('symbol', 'n/a')}",
        )
    with validation_cards[2]:
        render_metric_card(
            "Latest Drawdown",
            f"{float((latest_validation_run.get('summary') or {}).get('worst_test_drawdown_thb', 0.0) or 0.0):,.2f} THB",
            f"Slices {int((latest_validation_run.get('summary') or {}).get('completed_slices', 0) or 0)}",
        )
    with validation_cards[3]:
        render_metric_card(
            "Consistency Checks",
            str(len(recent_validation_consistency_checks)),
            "Replay determinism + leakage guards",
        )
    if recent_validation_runs:
        validation_run_rows = [
            {
                "created_at": row.get("created_at"),
                "validation_type": row.get("validation_type"),
                "status": row.get("status"),
                "symbol": row.get("symbol"),
                "data_source": row.get("data_source"),
                "mode": row.get("mode"),
                "date_from": row.get("date_from"),
                "date_to": row.get("date_to"),
                "completed_slices": int((row.get("summary") or {}).get("completed_slices", 0) or 0),
                "test_total_pnl_thb": float((row.get("summary") or {}).get("test_total_pnl_thb", 0.0) or 0.0),
                "test_profit_factor": float((row.get("summary") or {}).get("test_profit_factor", 0.0) or 0.0),
                "worst_test_drawdown_thb": float((row.get("summary") or {}).get("worst_test_drawdown_thb", 0.0) or 0.0),
            }
            for row in recent_validation_runs
        ]
        st.dataframe(validation_run_rows, width='stretch', hide_index=True)
    else:
        st.caption("No persisted validation runs found yet. Run walk-forward or time-series CV to populate this section.")

    validation_bottom_left, validation_bottom_right = st.columns([1.15, 0.85])
    with validation_bottom_left:
        render_section_intro(
            "Latest Validation Slices",
            "Train/test slice details from the most recent run for the current filter.",
            "Validation",
        )
        if recent_validation_slices:
            st.dataframe(
                [
                    {
                        "slice_no": row.get("slice_no"),
                        "status": row.get("status"),
                        "train_start_at": row.get("train_start_at"),
                        "train_end_at": row.get("train_end_at"),
                        "test_start_at": row.get("test_start_at"),
                        "test_end_at": row.get("test_end_at"),
                        "selected_variant": row.get("selected_variant"),
                        "train_pnl_thb": float((row.get("train_metrics") or {}).get("total_pnl_thb", 0.0) or 0.0),
                        "test_pnl_thb": float((row.get("test_metrics") or {}).get("total_pnl_thb", 0.0) or 0.0),
                        "test_trades": int((row.get("test_metrics") or {}).get("trades", 0) or 0),
                        "test_profit_factor": float((row.get("test_metrics") or {}).get("profit_factor", 0.0) or 0.0),
                    }
                    for row in recent_validation_slices
                ],
                width='stretch',
                hide_index=True,
            )
        else:
            st.caption("No validation slice rows to show yet.")
    with validation_bottom_right:
        render_section_intro(
            "Consistency Checks",
            "Repeated replay hashes should match for the same window and rule. This helps catch unstable or leaky backtests.",
            "Validation",
        )
        if recent_validation_consistency_checks:
            st.dataframe(
                [
                    {
                        "created_at": row.get("created_at"),
                        "status": row.get("status"),
                        "symbol": row.get("symbol"),
                        "data_source": row.get("data_source"),
                        "window_start_at": row.get("window_start_at"),
                        "window_end_at": row.get("window_end_at"),
                        "hashes": len(list((row.get("details") or {}).get("hashes") or [])),
                        "issues": len(list((row.get("details") or {}).get("issues") or [])),
                    }
                    for row in recent_validation_consistency_checks
                ],
                width='stretch',
                hide_index=True,
            )
        else:
            st.caption("No validation consistency checks recorded yet.")

    left, right = st.columns([1.15, 0.85])
    with left:
        render_section_intro("Symbol Summary", "Daily symbol-level rollup. Use this first before jumping into raw execution rows.", "Summary")
        if symbol_summary:
            st.dataframe(symbol_summary, width='stretch', hide_index=True)
        else:
            st.caption("No symbol summary rows for the current filter and date.")
    with right:
        render_section_intro("Recent Paper Trades", "Paper-side closeouts for the selected scope, useful for comparing against live outcomes.", "Paper")
        if recent_trades:
            st.dataframe(recent_trades, width='stretch', hide_index=True)
        else:
            st.caption("No paper trades stored for this filter yet.")

    fee_by_symbol_rows = []
    for row in symbol_summary:
        combined_trades = int(row.get("trades", 0) or 0) + int(row.get("live_closed_trades", 0) or 0)
        combined_pnl = float(row.get("pnl_thb", 0.0) or 0.0) + float(row.get("live_realized_pnl_thb", 0.0) or 0.0)
        combined_fee = float(row.get("combined_fee_thb", 0.0) or 0.0)
        estimated_gross = combined_pnl + combined_fee if combined_pnl > 0 else 0.0
        estimated_fee_drag = (combined_fee * 100.0 / estimated_gross) if estimated_gross > 0 else 0.0
        avg_pnl = (combined_pnl / combined_trades) if combined_trades else 0.0
        avg_fee = (combined_fee / combined_trades) if combined_trades else 0.0
        fee_guardrail, fee_note, _ = evaluate_fee_guardrail(
            trades=combined_trades,
            total_pnl_thb=combined_pnl,
            total_fee_thb=combined_fee,
            avg_pnl_thb=avg_pnl,
            avg_fee_thb=avg_fee,
            fee_drag_percent=estimated_fee_drag,
        )
        fee_by_symbol_rows.append(
            {
                "symbol": row.get("symbol"),
                "paper_fee_thb": row.get("paper_fee_thb", 0.0),
                "live_fee_thb": row.get("live_fee_thb", 0.0),
                "combined_fee_thb": combined_fee,
                "combined_realized_pnl_thb": combined_pnl,
                "closed_trades": combined_trades,
                "fee_drag_percent": estimated_fee_drag,
                "fee_guardrail": fee_guardrail,
                "fee_guardrail_note": fee_note,
            }
        )

    heavy_fee_rows = [row for row in fee_by_symbol_rows if str(row.get("fee_guardrail")) in {"FEE_HEAVY", "THIN_EDGE", "LOSS_AFTER_FEES"}]
    if heavy_fee_rows:
        render_callout(
            "Fee Pressure",
            f"{len(heavy_fee_rows)} symbol(s) are under fee pressure. Review the Fee By Symbol table before widening automation or reducing entry filters.",
            "warn",
        )
    else:
        render_callout(
            "Fee Pressure",
            "No symbol is currently flagged as fee-heavy for the selected report scope.",
            "good",
        )

    fee_left, fee_right = st.columns([1.0, 1.0])
    with fee_left:
        render_section_intro("Fee By Symbol", "Fee drag and realized contribution per symbol so you can see where the edge is being spent.", "Fees")
        if fee_by_symbol_rows:
            st.dataframe(fee_by_symbol_rows, width='stretch', hide_index=True)
        else:
            st.caption("No fee rows for the current filter yet.")
    with fee_right:
        render_section_intro("Fee Notes", "How fee numbers are derived and how to interpret the fee guardrail column.", "Fees")
        st.caption("Paper fees come from paper_trade_logs buy_fee_thb + sell_fee_thb.")
        st.caption("Live fees come from filled execution_orders response_json.result.fee.")
        st.caption("Live realized PnL is estimated from filled buy/sell execution history using FIFO cost basis.")
        st.caption("Fee guardrail flags symbols whose net edge is too thin relative to trading fees.")

    render_section_intro(
        "Recent Trade Journal",
        "Structured intent records across live and shadow-live paths, including blocked actions before they reach the exchange.",
        "Journal",
    )
    if recent_trade_journal:
        st.dataframe(recent_trade_journal, width='stretch', hide_index=True)
    else:
        st.caption("No trade journal rows recorded for this filter yet.")

    bottom_left, bottom_right = st.columns([1.0, 1.0])
    with bottom_left:
        render_section_intro("Recent Execution Orders", "Latest live execution rows for the selected report scope.", "Execution")
        if recent_execution_orders:
            st.dataframe(recent_execution_orders, width='stretch', hide_index=True)
        else:
            st.caption("No execution orders stored for this filter yet.")
        render_section_intro("Recent Auto Exit Events", "Auto-exit runtime events that help explain why the engine tried to close positions.", "Execution")
        if recent_auto_exit_events:
            st.dataframe(recent_auto_exit_events, width='stretch', hide_index=True)
        else:
            st.caption("No auto-exit events stored for this filter yet.")
    with bottom_right:
        render_section_intro("Recent Runtime Errors", "Read this after the summary if something still looks inconsistent.", "Diagnostics")
        if recent_errors:
            st.dataframe(recent_errors, width='stretch', hide_index=True)
        else:
            st.caption("No recent runtime errors recorded.")


