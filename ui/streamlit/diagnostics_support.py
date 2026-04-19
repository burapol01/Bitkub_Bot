from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

import streamlit as st

from services.account_service import account_snapshot_errors, build_live_holdings_snapshot
from services.audit_service import audit_event
from services.backup_service import (
    create_runtime_backup,
    latest_runtime_backup_summary,
    list_runtime_backups,
)
from services.db_service import (
    archive_sqlite_retention,
    cleanup_sqlite_retention,
    fetch_diagnostics_page_dataset,
    fetch_execution_console_summary,
    fetch_logs_page_dataset,
    insert_runtime_event,
)
from services.reconciliation_service import summarize_live_reconciliation
from services.telegram_service import telegram_settings_snapshot
from ui.streamlit.styles import badge, render_callout, render_metric_card, render_section_intro
from ui.streamlit.strategy_support import evaluate_fee_guardrail
from utils.time_utils import now_dt, now_text


def classify_runtime_event(row: dict[str, Any]) -> dict[str, Any]:
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


def current_private_api_issues(private_ctx: dict[str, Any]) -> list[dict[str, Any]]:
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


def render_reconciliation_block(live_reconciliation: dict[str, Any]) -> None:
    groups = (
        ("warnings", "warn"),
        ("missing_locally", "bad"),
        ("partially_filled_orders", "warn"),
        ("stale_pending_orders", "warn"),
        ("orders_without_exchange_id", "bad"),
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


def _runtime_event_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "created_at": row.get("created_at"),
            "severity": row.get("severity"),
            "category": row.get("category"),
            "topic": row.get("topic"),
            "event_type": row.get("event_type"),
            "message": row.get("message"),
            "hint": row.get("hint"),
        }
        for row in rows
    ]


def _audit_event_table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "created_at": row.get("created_at"),
            "action_type": row.get("action_type"),
            "actor_type": row.get("actor_type"),
            "source": row.get("source"),
            "target": row.get("target_id") or row.get("target_type"),
            "symbol": row.get("symbol"),
            "status": row.get("status"),
            "message": row.get("message"),
        }
        for row in rows
    ]


@st.cache_data(ttl=10, show_spinner=False)
def _cached_logs_page_payload(today: str) -> dict[str, Any]:
    dataset = fetch_logs_page_dataset(today=today)
    return {
        "latest_account_snapshot": dataset.get("latest_account_snapshot"),
        "historical_rows": [
            classify_runtime_event(row)
            for row in list(dataset.get("historical_rows") or [])
        ],
        "error_rows": [
            classify_runtime_event(row)
            for row in list(dataset.get("error_rows") or [])
        ],
        "telegram_rows": list(dataset.get("telegram_rows") or []),
        "telegram_command_rows": list(dataset.get("telegram_command_rows") or []),
        "audit_rows": list(dataset.get("audit_rows") or []),
        "today_reporting": dict(dataset.get("today_reporting") or {}),
    }


def _current_issue_rows_from_latest_snapshot(
    latest_account_snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not latest_account_snapshot:
        return []

    snapshot = latest_account_snapshot.get("snapshot")
    if not isinstance(snapshot, dict):
        return []

    current_issue_rows = current_private_api_issues(
        {
            "errors": account_snapshot_errors(snapshot),
            "account_snapshot": snapshot,
        }
    )
    snapshot_created_at = str(latest_account_snapshot.get("created_at") or "latest snapshot")
    for row in current_issue_rows:
        row["created_at"] = snapshot_created_at
    return current_issue_rows


@st.cache_data(ttl=10, show_spinner=False)
def _cached_execution_console_summary() -> dict[str, Any]:
    return fetch_execution_console_summary()


@st.cache_data(ttl=15, show_spinner=False)
def _cached_diagnostics_page_dataset() -> dict[str, Any]:
    return fetch_diagnostics_page_dataset()


def _show_retention_feedback() -> None:
    payload = st.session_state.get("diagnostics_retention_feedback")
    if not payload:
        return

    title = str(payload.get("title", "Retention result"))
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


def _set_retention_feedback(*, title: str, lines: list[str], tone: str = "success") -> None:
    st.session_state["diagnostics_retention_feedback"] = {
        "title": title,
        "lines": lines,
        "tone": tone,
    }


def _show_backup_feedback() -> None:
    payload = st.session_state.get("diagnostics_backup_feedback")
    if not payload:
        return

    title = str(payload.get("title", "Backup result"))
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


def _set_backup_feedback(*, title: str, lines: list[str], tone: str = "success") -> None:
    st.session_state["diagnostics_backup_feedback"] = {
        "title": title,
        "lines": lines,
        "tone": tone,
    }


def _retention_hot_days_for_table(config: dict[str, Any], table_name: str) -> int:
    return int(
        {
            "market_snapshots": config.get("market_snapshot_hot_retention_days", 90),
            "signal_logs": config.get("signal_log_hot_retention_days", 180),
            "account_snapshots": config.get("account_snapshot_hot_retention_days", 90),
            "reconciliation_results": config.get("reconciliation_hot_retention_days", 90),
            "runtime_events": config.get("runtime_event_retention_days", 30),
        }.get(table_name, 30)
    )


def _retention_archive_enabled_for_table(config: dict[str, Any], table_name: str) -> bool:
    return bool(
        {
            "market_snapshots": config.get("market_snapshot_archive_enabled", True),
            "signal_logs": config.get("signal_log_archive_enabled", True),
            "account_snapshots": config.get("account_snapshot_archive_enabled", True),
            "reconciliation_results": config.get("reconciliation_archive_enabled", True),
            "runtime_events": False,
        }.get(table_name, False)
    )


def _build_retention_rows(
    *,
    config: dict[str, Any],
    retention_status: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for row in list(retention_status.get("tables") or []):
        table_name = str(row.get("table_name") or "")
        latest_archive = dict(row.get("latest_archive_run") or {})
        rows.append(
            {
                "table": table_name,
                "record_count": int(row.get("record_count", 0) or 0),
                "oldest_at": row.get("oldest_at"),
                "newest_at": row.get("newest_at"),
                "hot_retention_days": _retention_hot_days_for_table(config, table_name),
                "archive_enabled": _retention_archive_enabled_for_table(config, table_name),
                "archive_status": latest_archive.get("archive_status") or "n/a",
                "cleanup_status": latest_archive.get("cleanup_status") or "n/a",
                "archive_date": latest_archive.get("archive_date"),
                "archive_time": latest_archive.get("completed_at"),
                "cleanup_time": latest_archive.get("cleanup_completed_at"),
                "cleanup_deleted_count": int(
                    latest_archive.get("cleanup_deleted_count", 0) or 0
                ),
                "archive_path": latest_archive.get("archive_path"),
            }
        )

    return rows


def _run_retention_action(*, config: dict[str, Any], action: str) -> None:
    try:
        if action == "archive":
            summary = archive_sqlite_retention(config=config)
            archived_total = int(summary.get("archived_total", 0) or 0)
            errors = list(summary.get("errors", []))
            tone = "warning" if errors else "success"
            _set_retention_feedback(
                title=(
                    f"Archived {archived_total} analytical record(s)"
                    if not errors
                    else f"Archive completed with {len(errors)} warning(s)"
                ),
                lines=[
                    f"Archive dir: {summary.get('archive_dir')}",
                    f"Compression: {summary.get('archive_format')} + {summary.get('archive_compression')}",
                    f"Errors: {', '.join(errors) if errors else 'none'}",
                ],
                tone=tone,
            )
            insert_runtime_event(
                created_at=now_text(),
                event_type="sqlite_retention_archive",
                severity="warning" if errors else "info",
                message=f"SQLite retention archive completed; archived {archived_total} records",
                details=summary,
            )
            audit_event(
                action_type="retention_archive",
                actor_type="ui",
                source="streamlit_diagnostics",
                target_type="sqlite",
                target_id="retention",
                status="failed" if errors else "succeeded",
                message="SQLite retention archive completed",
                reason='; '.join(errors) if errors else None,
                metadata=summary,
            )
        elif action == "cleanup":
            summary = cleanup_sqlite_retention(config=config)
            archived_total = int(summary.get("archived_total", 0) or 0)
            deleted_total = int(summary.get("deleted_total", 0) or 0)
            archive_errors = list((summary.get("archive") or {}).get("errors", []))
            tone = "warning" if archive_errors else "success"
            _set_retention_feedback(
                title=(
                    f"Cleanup completed; archived {archived_total} and removed {deleted_total}"
                    if not archive_errors
                    else f"Cleanup completed with {len(archive_errors)} archive warning(s)"
                ),
                lines=[
                    f"Archive dir: {(summary.get('archive') or {}).get('archive_dir')}",
                    f"Archive errors: {', '.join(archive_errors) if archive_errors else 'none'}",
                    f"Deleted total: {deleted_total}",
                ],
                tone=tone,
            )
            insert_runtime_event(
                created_at=now_text(),
                event_type="sqlite_retention_cleanup",
                severity="warning" if archive_errors else "info",
                message=(
                    "SQLite retention cleanup completed; "
                    f"archived {archived_total} records and removed {deleted_total} rows"
                ),
                details=summary,
            )
            audit_event(
                action_type="retention_cleanup",
                actor_type="ui",
                source="streamlit_diagnostics",
                target_type="sqlite",
                target_id="retention",
                status="failed" if archive_errors else "succeeded",
                message="SQLite retention cleanup completed",
                reason='; '.join(archive_errors) if archive_errors else None,
                metadata=summary,
            )
        else:
            raise ValueError(f"unsupported retention action: {action}")

        _cached_diagnostics_page_dataset.clear()
        st.rerun()
    except Exception as e:
        _set_retention_feedback(
            title="Retention maintenance failed",
            lines=[str(e)],
            tone="error",
        )
        audit_event(
            action_type=("retention_archive" if action == "archive" else "retention_cleanup"),
            actor_type="ui",
            source="streamlit_diagnostics",
            target_type="sqlite",
            target_id="retention",
            status="failed",
            message="Retention maintenance failed",
            reason=str(e),
            metadata={"action": action},
        )
        st.error(str(e))


def _run_backup_action(*, config: dict[str, Any]) -> None:
    try:
        summary = create_runtime_backup(
            backup_dir_value=config.get("backup_dir"),
            backup_retention_days=int(config.get("backup_retention_days", 90) or 90),
            include_env_file=bool(config.get("backup_include_env_file", False)),
        )
        success = bool(summary.get("success", False))
        warnings = list(summary.get("warnings", []))
        errors = list(summary.get("errors", []))
        tone = "error" if not success else "warning" if warnings else "success"
        _set_backup_feedback(
            title="Backup completed" if success else "Backup failed",
            lines=[
                f"Bundle: {summary.get('bundle_path', 'n/a')}",
                f"Size: {int(summary.get('bundle_size_bytes', 0) or 0):,} bytes",
                (
                    f"Assets: {int(summary.get('captured_asset_count', 0) or 0)} captured | "
                    f"{int(summary.get('failed_asset_count', 0) or 0)} failed | "
                    f"{int(summary.get('skipped_asset_count', 0) or 0)} skipped"
                ),
                f"Warnings: {', '.join(warnings) if warnings else 'none'}",
                f"Errors: {', '.join(errors) if errors else 'none'}",
            ],
            tone=tone,
        )
    except Exception as e:
        _set_backup_feedback(
            title="Backup failed",
            lines=[str(e)],
            tone="error",
        )
        st.error(str(e))


def render_logs_page(*, config: dict[str, Any], today: str) -> None:
    render_section_intro(
        "Logs & Errors",
        "Start with the current issues and fee watch, then go deeper into runtime history only when needed.",
        "Diagnostics",
    )

    logs_payload = _cached_logs_page_payload(today)
    latest_account_snapshot = logs_payload.get("latest_account_snapshot")
    current_issue_rows = _current_issue_rows_from_latest_snapshot(latest_account_snapshot)
    historical_rows = list(logs_payload.get("historical_rows") or [])
    error_rows = list(logs_payload.get("error_rows") or [])
    telegram_rows = list(logs_payload.get("telegram_rows") or [])
    telegram_command_rows = list(logs_payload.get("telegram_command_rows") or [])
    audit_rows = list(logs_payload.get("audit_rows") or [])
    telegram_settings = telegram_settings_snapshot(config)
    today_reporting = dict(logs_payload.get("today_reporting") or {})
    paper_realized_today = float(today_reporting.get("paper_pnl_thb", 0.0) or 0.0)
    live_realized_today = float(today_reporting.get("live_realized_pnl_thb", 0.0) or 0.0)
    paper_fee_today = float(today_reporting.get("paper_fee_thb", 0.0) or 0.0)
    live_fee_today = float(today_reporting.get("live_fee_thb", 0.0) or 0.0)
    combined_realized_today = paper_realized_today + live_realized_today
    combined_fee_today = paper_fee_today + live_fee_today
    combined_trades_today = int(today_reporting.get("paper_trades", 0) or 0) + int(
        today_reporting.get("live_closed_trades", 0) or 0
    )
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

    category_counts: dict[str, int] = defaultdict(int)
    for row in current_issue_rows + historical_rows:
        category_counts[str(row.get("category") or "General")] += 1

    if latest_account_snapshot is None:
        render_callout(
            "No Account Snapshot Recorded Yet",
            "Logs are showing persisted runtime history, but the engine has not written an account snapshot yet.",
            "info",
        )
    elif current_issue_rows:
        render_callout(
            "Current Issues Need Attention",
            f"There are {len(current_issue_rows)} current issue(s) in the latest snapshot. Start with the Current Issues table before reviewing historical events.",
            "warn",
        )
    else:
        render_callout(
            "Current Snapshot Looks Clean",
            "No current private-API or open-order issues were detected in the latest snapshot.",
            "good",
        )

    card1, card2, card3, card4 = st.columns(4)
    with card1:
        render_metric_card("Current Issues", str(len(current_issue_rows)), "Current snapshot only")
    with card2:
        render_metric_card("Historical Events", str(len(historical_rows)), "Last 200 runtime events")
    with card3:
        render_metric_card("Error Events", str(len(error_rows)), "Latest 200 persisted runtime errors")
    with card4:
        top_category = max(category_counts, key=category_counts.get) if category_counts else "None"
        render_metric_card("Top Category", top_category, f"Telegram queued {len(telegram_rows)}")

    st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
    current_left, current_right = st.columns([1.0, 1.0])
    with current_left:
        st.markdown('<div class="panel-title">Current Issues</div>', unsafe_allow_html=True)
        if current_issue_rows:
            st.dataframe(current_issue_rows, width='stretch', hide_index=True)
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
                width='stretch',
                hide_index=True,
            )
        else:
            st.caption("No runtime categories recorded yet.")

    render_section_intro("Telegram Delivery Readiness", "Quick readiness check before relying on Telegram notifications or control commands.", "Telegram")
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

    render_section_intro("Fee Watch", "Daily fee drag summary so you can spot when the strategy is paying too much for its edge.", "Fees")
    fee_cols = st.columns(4)
    with fee_cols[0]:
        render_metric_card("Combined Fee Today", f"{combined_fee_today:,.2f} THB", f"Trades {combined_trades_today}")
    with fee_cols[1]:
        render_metric_card("Combined Realized", f"{combined_realized_today:,.2f} THB", f"Paper {paper_realized_today:,.2f} | Live {live_realized_today:,.2f}")
    with fee_cols[2]:
        render_metric_card("Fee Drag", f"{combined_fee_drag_today:.2f}%", f"Paper {paper_fee_today:,.2f} | Live {live_fee_today:,.2f}")
    with fee_cols[3]:
        render_metric_card("Fee Guardrail", fee_guardrail, today)
    render_callout(
        "Fee Watch",
        f"{fee_guardrail} | {fee_guardrail_note}",
        "bad" if fee_guardrail == "LOSS_AFTER_FEES" else "warn" if fee_guardrail in {"FEE_HEAVY", "THIN_EDGE"} else "good",
    )

    latest_auto_entry_review = next(
        (
            row
            for row in historical_rows
            if str(row.get("event_type") or "") == "auto_live_entry_review"
        ),
        None,
    )

    render_section_intro("Latest Auto Entry Review", "Use this to understand why the bot almost entered, did enter, or kept rejecting candidates.", "Auto Entry")
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

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
        review_left, review_right = st.columns([1.0, 1.0])
        with review_left:
            if review_candidates:
                st.dataframe(review_candidates, width='stretch', hide_index=True)
            else:
                st.caption("No auto-entry candidates passed the current filters in the latest review.")
        with review_right:
            if review_rejected:
                st.dataframe(review_rejected, width='stretch', hide_index=True)
            else:
                st.caption("No rejected candidates were recorded in the latest review.")
    else:
        st.caption("No auto-entry review has been recorded yet.")

    error_event_types = ["ALL"] + sorted(
        {str(row.get("event_type") or "runtime_error") for row in error_rows}
    )
    selected_error_event_type = st.selectbox(
        "Error Event Filter",
        error_event_types,
        index=0,
        key="logs_error_event_filter",
    )
    filtered_error_rows = [
        row
        for row in error_rows
        if selected_error_event_type == "ALL"
        or str(row.get("event_type") or "runtime_error") == selected_error_event_type
    ]

    render_section_intro(
        "Error-Only Runtime Events",
        "Fast path for real failures only. Use this before the broader history table when you are debugging.",
        "Errors",
    )
    if filtered_error_rows:
        st.dataframe(
            _runtime_event_table_rows(filtered_error_rows),
            width='stretch',
            hide_index=True,
        )
    else:
        st.caption("No runtime errors match the selected error filter.")

    with st.expander("Error Details", expanded=False):
        error_detail_rows = [
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
            for row in filtered_error_rows[:50]
        ]
        if error_detail_rows:
            st.json(error_detail_rows, expanded=False)
        else:
            st.caption("No error details to show.")

    audit_action_types = ["ALL"] + sorted({str(row.get("action_type") or "audit") for row in audit_rows})
    audit_statuses = ["ALL"] + sorted({str(row.get("status") or "unknown") for row in audit_rows})
    audit_symbols = ["ALL"] + sorted({str(row.get("symbol") or "") for row in audit_rows if str(row.get("symbol") or "")})
    audit_filter_cols = st.columns(4)
    with audit_filter_cols[0]:
        selected_audit_action = st.selectbox("Audit Action Filter", audit_action_types, index=0, key="logs_audit_action_filter")
    with audit_filter_cols[1]:
        selected_audit_status = st.selectbox("Audit Status Filter", audit_statuses, index=0, key="logs_audit_status_filter")
    with audit_filter_cols[2]:
        selected_audit_symbol = st.selectbox("Audit Symbol Filter", audit_symbols, index=0, key="logs_audit_symbol_filter")
    with audit_filter_cols[3]:
        recent_only = st.checkbox("Recent Audit Only (24h)", value=True, key="logs_audit_recent_only")

    audit_cutoff = (now_dt() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    filtered_audit_rows = [
        row
        for row in audit_rows
        if (selected_audit_action == "ALL" or str(row.get("action_type") or "audit") == selected_audit_action)
        and (selected_audit_status == "ALL" or str(row.get("status") or "unknown") == selected_audit_status)
        and (selected_audit_symbol == "ALL" or str(row.get("symbol") or "") == selected_audit_symbol)
        and (not recent_only or str(row.get("created_at") or "") >= audit_cutoff)
    ]

    render_section_intro("Audit Trail", "Structured operator and system actions live here. Filter this table before falling back to broader runtime history.", "Audit")
    if filtered_audit_rows:
        st.dataframe(
            _audit_event_table_rows(filtered_audit_rows),
            width='stretch',
            hide_index=True,
        )
        with st.expander("Audit Details", expanded=False):
            st.json(filtered_audit_rows[:50], expanded=False)
    else:
        st.caption("No audit events match the selected filters.")

    log_categories = ["ALL"] + sorted({str(row.get("category") or "General") for row in historical_rows})
    selected_category = st.selectbox("Log Category Filter", log_categories, index=0, key="logs_category_filter")
    filtered_rows = [
        row
        for row in historical_rows
        if selected_category == "ALL" or str(row.get("category")) == selected_category
    ]

    render_section_intro("Historical Runtime Events", "Lower-signal history lives here. Filter by category after you check current issues.", "History")
    if filtered_rows:
        st.dataframe(
            _runtime_event_table_rows(filtered_rows),
            width='stretch',
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

    render_section_intro("Telegram Outbox", "Queued and recently sent notifications, useful when checking delivery or anti-spam behavior.", "Messaging")
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
            width='stretch',
            hide_index=True,
        )
        with st.expander("Telegram Outbox Details", expanded=False):
            st.json(telegram_rows[:25], expanded=False)
    else:
        st.caption("No Telegram notifications have been queued yet.")

    render_section_intro("Telegram Command Log", "Recent control interactions and the engine response captured for each command.", "Messaging")
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
            width='stretch',
            hide_index=True,
        )
        with st.expander("Telegram Command Details", expanded=False):
            st.json(telegram_command_rows[:25], expanded=False)
    else:
        st.caption("No Telegram commands have been processed yet.")


def render_diagnostics_page(
    *,
    config: dict[str, Any],
    today: str,
    private_ctx: dict[str, Any],
    latest_prices: dict[str, float],
) -> None:
    diagnostics_dataset = _cached_diagnostics_page_dataset()
    db_summary = dict(diagnostics_dataset.get("db_summary") or {})
    dashboard_summary = dict(diagnostics_dataset.get("summary") or {})
    execution_console_counts = dict(diagnostics_dataset.get("execution_console_counts") or {})
    open_execution_orders = list(diagnostics_dataset.get("open_execution_orders") or [])
    latest_filled_execution_orders = dict(
        diagnostics_dataset.get("latest_filled_execution_orders_by_symbol") or {}
    )
    live_holdings_rows = build_live_holdings_snapshot(
        account_snapshot=private_ctx["account_snapshot"],
        latest_prices=latest_prices,
        latest_filled_execution_orders=latest_filled_execution_orders,
    )
    use_deep_reconciliation = st.toggle(
        "Deep Exchange Reconciliation",
        value=False,
        key="diagnostics_deep_reconciliation",
        help="Slower but more thorough. When enabled, Diagnostics may call exchange order_info per open execution order to confirm anything missing from the latest account snapshot.",
    )
    live_reconciliation = summarize_live_reconciliation(
        execution_orders=open_execution_orders,
        live_holdings_rows=live_holdings_rows,
        account_snapshot=private_ctx["account_snapshot"],
        private_client=private_ctx["client"] if use_deep_reconciliation else None,
    )
    latest_account_snapshot = dashboard_summary.get("latest_account_snapshot")
    latest_reconciliation = dashboard_summary.get("latest_reconciliation")
    latest_state_reconciliation = diagnostics_dataset.get("latest_state_reconciliation") or {}
    recent_state_reconciliation_runs = list(
        diagnostics_dataset.get("recent_state_reconciliation_runs") or []
    )
    latest_execution_order = dashboard_summary.get("latest_execution_order")
    retention_status = dict(diagnostics_dataset.get("retention_status") or {})
    retention_rows = _build_retention_rows(
        config=config,
        retention_status=retention_status,
    )
    latest_archive_run = dict(retention_status.get("latest_archive_run") or {})
    latest_cleanup_run = dict(
        retention_status.get("latest_cleanup") or db_summary.get("latest_cleanup") or {}
    )
    _show_retention_feedback()
    _show_backup_feedback()

    storage_card1, storage_card2, storage_card3, storage_card4 = st.columns(4)
    table_counts = dict(db_summary.get("table_counts", {}))

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

    st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
    col1, col2 = st.columns([0.95, 1.05])
    with col1:
        st.markdown('<div class="panel-title">SQLite Health</div>', unsafe_allow_html=True)
        st.caption(
            f"Archive dir: {config.get('archive_dir', 'data/archive')}"
        )
        st.caption(
            "Archive flow "
            f"{'ON' if bool(config.get('archive_enabled', True)) else 'OFF'} | "
            f"format {config.get('archive_format', 'csv')} | "
            f"compression {config.get('archive_compression', 'gzip')}"
        )
        st.caption(
            f"Latest archive: {latest_archive_run.get('completed_at', 'n/a')} | "
            f"{latest_archive_run.get('archive_status', 'n/a')}"
        )
        st.caption(
            f"Latest cleanup: {latest_cleanup_run.get('created_at', 'n/a')}"
        )
        st.caption(str(latest_cleanup_run.get("message", "No cleanup history")))
        if retention_rows:
            st.dataframe(retention_rows, width='stretch', hide_index=True)
        else:
            st.caption("No retention rows available yet.")

        action_cols = st.columns(2)
        with action_cols[0]:
            if st.button(
                "Run Archive Now",
                width="stretch",
                key="diagnostics_run_archive_now",
            ):
                _run_retention_action(config=config, action="archive")
        with action_cols[1]:
            if st.button(
                "Run Cleanup Now",
                width="stretch",
                key="diagnostics_run_cleanup_now",
            ):
                _run_retention_action(config=config, action="cleanup")

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
        if latest_state_reconciliation:
            latest_rows.append(
                {
                    "type": "state_reconciliation",
                    "time": latest_state_reconciliation.get("created_at"),
                    "status": latest_state_reconciliation.get("status"),
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
        st.dataframe(latest_rows, width='stretch', hide_index=True)

        st.markdown('<div class="panel-title">Backup & Restore</div>', unsafe_allow_html=True)
        backup_root_summary = latest_runtime_backup_summary(
            backup_root_value=config.get("backup_dir")
        )
        backup_root_display = str(
            backup_root_summary.get("backup_root") or config.get("backup_dir", "backups")
        )
        st.caption(f"Backup root: {backup_root_display}")
        st.caption(
            "Retention "
            f"{int(config.get('backup_retention_days', 90) or 90)} day(s) | "
            f".env included {'ON' if bool(config.get('backup_include_env_file', False)) else 'OFF'}"
        )
        if backup_root_summary:
            st.caption(
                f"Latest backup: {backup_root_summary.get('created_at', 'n/a')} | "
                f"{backup_root_summary.get('bundle_name', 'n/a')}"
            )
            st.caption(
                f"Size: {int(backup_root_summary.get('bundle_size_bytes', 0) or 0):,} bytes | "
                f"Assets: {int(backup_root_summary.get('captured_asset_count', 0) or 0)} captured"
            )
            if backup_root_summary.get("warnings"):
                st.caption(f"Warnings: {', '.join(backup_root_summary.get('warnings', []))}")
            if backup_root_summary.get("errors"):
                st.caption(f"Errors: {', '.join(backup_root_summary.get('errors', []))}")
        else:
            st.caption("No backup bundles have been created yet.")

        recent_backups = list_runtime_backups(
            backup_root_value=config.get("backup_dir"),
            limit=5,
        )
        if recent_backups:
            st.dataframe(
                [
                    {
                        "created_at": row.get("created_at"),
                        "bundle": row.get("bundle_name"),
                        "size_bytes": int(row.get("bundle_size_bytes", 0) or 0),
                        "assets": int(row.get("captured_asset_count", 0) or 0),
                        "success": "YES" if row.get("success") else "NO",
                    }
                    for row in recent_backups
                ],
                width='stretch',
                hide_index=True,
            )
        else:
            st.caption("Backup inventory is empty.")

        if st.button(
            "Run Backup Now",
            width="stretch",
            key="diagnostics_run_backup_now",
        ):
            _run_backup_action(config=config)
            st.rerun()

        st.caption(
            "Restore offline with scripts/restore_runtime.py after stopping the bot and Streamlit, "
            "then verify config and runtime state before resuming trades."
        )
    with col2:
        st.markdown('<div class="panel-title">Reconciliation Health</div>', unsafe_allow_html=True)
        if latest_state_reconciliation:
            latest_run_source = str(latest_state_reconciliation.get("source") or "n/a")
            latest_run_status = str(latest_state_reconciliation.get("status") or "n/a")
            latest_account_sync = str(
                latest_state_reconciliation.get("account_sync_status") or "n/a"
            )
            latest_runtime_state_status = str(
                latest_state_reconciliation.get("runtime_state_status") or "n/a"
            )
            latest_notes = dict(latest_state_reconciliation.get("notes") or {})
            latest_mismatch_summary = dict(
                latest_state_reconciliation.get("mismatch_summary") or {}
            )
            latest_mismatch_details = dict(
                latest_state_reconciliation.get("mismatch_details") or {}
            )
            latest_correction_summary = dict(
                latest_state_reconciliation.get("correction_summary") or {}
            )

            health_cols = st.columns(4)
            with health_cols[0]:
                render_metric_card(
                    "Last Run",
                    str(latest_state_reconciliation.get("created_at") or "n/a"),
                    latest_run_source,
                )
            with health_cols[1]:
                render_metric_card(
                    "Status",
                    latest_run_status.upper(),
                    f"account {latest_account_sync}",
                )
            with health_cols[2]:
                render_metric_card(
                    "Unresolved",
                    str(int(latest_state_reconciliation.get("unresolved_count", 0) or 0)),
                    f"stale pending {int(latest_state_reconciliation.get('stale_pending_count', 0) or 0)}",
                )
            with health_cols[3]:
                render_metric_card(
                    "Corrected",
                    str(int(latest_state_reconciliation.get("corrected_order_count", 0) or 0)),
                    str(latest_notes.get("exchange_snapshot_created_at") or "exchange sync n/a"),
                )

            st.caption(
                f"Runtime state: {latest_runtime_state_status} | "
                f"local open orders {int(latest_state_reconciliation.get('local_open_orders_count', 0) or 0)} | "
                f"exchange open orders {int(latest_state_reconciliation.get('exchange_open_orders_count', 0) or 0)}"
            )

            mismatch_rows = [
                {"category": key, "count": int(value or 0)}
                for key, value in latest_mismatch_summary.items()
            ]
            if mismatch_rows:
                st.dataframe(mismatch_rows, width="stretch", hide_index=True)

            if recent_state_reconciliation_runs:
                st.dataframe(
                    [
                        {
                            "created_at": row.get("created_at"),
                            "source": row.get("source"),
                            "status": row.get("status"),
                            "account_sync": row.get("account_sync_status"),
                            "runtime_state": row.get("runtime_state_status"),
                            "local_open": int(row.get("local_open_orders_count", 0) or 0),
                            "exchange_open": int(row.get("exchange_open_orders_count", 0) or 0),
                            "corrected": int(row.get("corrected_order_count", 0) or 0),
                            "unresolved": int(row.get("unresolved_count", 0) or 0),
                            "stale_pending": int(row.get("stale_pending_count", 0) or 0),
                        }
                        for row in recent_state_reconciliation_runs[:10]
                    ],
                    width="stretch",
                    hide_index=True,
                )

            with st.expander("Latest Reconciliation Details", expanded=False):
                st.json(
                    {
                        "mismatch_summary": latest_mismatch_summary,
                        "mismatch_details": latest_mismatch_details,
                        "correction_summary": latest_correction_summary,
                        "notes": latest_notes,
                    },
                    expanded=False,
                )
        else:
            st.caption("No structured reconciliation runs have been recorded yet.")

        st.markdown('<div class="panel-title">Live Reconciliation</div>', unsafe_allow_html=True)
        st.caption(
            "Mode: deep exchange confirmation"
            if use_deep_reconciliation
            else "Mode: fast snapshot check only"
        )
        render_reconciliation_block(live_reconciliation)
        st.markdown('<div class="panel-title">Execution Console Summary</div>', unsafe_allow_html=True)
        summary_rows = [
            {
                "group": "open_orders",
                "count": int(execution_console_counts.get("open_orders", 0)),
            },
            {
                "group": "recent_orders",
                "count": int(execution_console_counts.get("recent_orders", 0)),
            },
            {
                "group": "recent_events",
                "count": int(execution_console_counts.get("recent_events", 0)),
            },
        ]
        st.dataframe(summary_rows, width='stretch', hide_index=True)
        load_execution_details = st.toggle(
            "Load Execution Details",
            value=False,
            key="diagnostics_load_execution_details",
            help="Disabled by default to keep Diagnostics faster. Turn this on only when you need the full open-order and event payloads.",
        )
        if load_execution_details:
            with st.expander("Execution Details", expanded=False):
                st.json(_cached_execution_console_summary(), expanded=False)
        else:
            st.caption("Execution details are skipped by default for faster page loads.")
