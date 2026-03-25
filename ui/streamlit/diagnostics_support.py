from __future__ import annotations

from collections import defaultdict
from typing import Any

import streamlit as st

from config import reload_config
from services.account_service import build_live_holdings_snapshot
from services.db_service import (
    fetch_dashboard_summary,
    fetch_db_maintenance_summary,
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_recent_telegram_command_log,
    fetch_recent_telegram_outbox,
    fetch_runtime_event_log,
)
from services.reconciliation_service import summarize_live_reconciliation
from services.telegram_service import telegram_settings_snapshot
from ui.streamlit.styles import badge, render_metric_card


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


def render_logs_page(*, private_ctx: dict[str, Any]) -> None:
    st.markdown('<div class="panel-title">Logs & Errors</div>', unsafe_allow_html=True)
    st.caption(
        "Use this page to separate current issues from historical runtime events. Categories and hints are there to speed up troubleshooting."
    )

    current_issue_rows = current_private_api_issues(private_ctx)
    historical_rows = [classify_runtime_event(row) for row in fetch_runtime_event_log(limit=200)]
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
            width='stretch',
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
            width='stretch',
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
            st.dataframe(retention_rows, width='stretch', hide_index=True)

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
        st.dataframe(latest_rows, width='stretch', hide_index=True)
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
        st.dataframe(summary_rows, width='stretch', hide_index=True)
        with st.expander("Execution Details", expanded=False):
            st.json(execution_console_summary, expanded=False)
