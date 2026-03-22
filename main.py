import msvcrt
import time
from collections.abc import Callable

from clients.bitkub_client import get_ticker
from clients.bitkub_private_client import (
    BitkubMissingCredentialsError,
    BitkubPrivateClient,
    BitkubPrivateClientError,
)
from config import CONFIG_PATH, reload_config, summarize_config_changes
from core.strategy import get_zone, zone_changed
from core.trade_engine import handle_symbol, import_wallet_position
from services.account_service import (
    account_snapshot_errors,
    build_live_holdings_snapshot,
    fetch_account_snapshot,
    open_orders_error_map,
    summarize_account_capabilities,
)
from services.db_service import (
    DB_PATH,
    fetch_db_maintenance_summary,
    fetch_dashboard_summary,
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_reporting_summary,
    init_db,
    insert_account_snapshot,
    insert_execution_order,
    insert_execution_order_event,
    insert_market_snapshot,
    insert_reconciliation_result,
    insert_runtime_event,
    prune_sqlite_retention,
    update_execution_order,
)
from services.execution_service import (
    LiveExecutionGuardrailError,
    build_live_execution_guardrails,
    cancel_live_order,
    evaluate_live_exit_candidates,
    refresh_live_order_from_exchange,
    submit_auto_live_exit_order,
    submit_manual_live_order,
)
from services.log_service import (
    ensure_signal_log_file,
    ensure_trade_log_file,
    write_signal_log,
)
from services.order_service import get_order_foundation_status, probe_order_foundation
from services.reconciliation_service import (
    extract_available_balances,
    extract_open_orders_by_symbol,
    reconcile_execution_orders_with_exchange,
    reconcile_positions_with_balances,
    summarize_live_reconciliation,
    symbol_to_asset,
)
from services.state_service import (
    STATE_FILE_PATH,
    load_runtime_state,
    save_runtime_state,
)
from services.ui_service import (
    divider,
    print_account_snapshot,
    print_database_summary,
    print_daily_stats_snapshot,
    print_execution_orders_snapshot,
    print_health_snapshot,
    print_live_holdings_snapshot,
    print_market_table,
    print_open_positions_snapshot,
    print_order_probe,
    print_reporting_summary,
    position_detail_text,
    render_header,
    section_title,
)
from state import cooldowns, daily_stats, last_zones, positions
from utils.time_utils import now_dt, now_text, today_key


def active_cooldown_rows() -> list[tuple[str, str, int]]:
    current_time = now_dt()
    rows = []

    for symbol, cooldown_until in cooldowns.items():
        remaining_seconds = int((cooldown_until - current_time).total_seconds())
        if remaining_seconds > 0:
            rows.append(
                (symbol, cooldown_until.strftime("%Y-%m-%d %H:%M:%S"), remaining_seconds)
            )

    return sorted(rows, key=lambda item: item[0])


def daily_totals() -> tuple[int, int, int, float]:
    today_stats = daily_stats.get(today_key(), {})
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0

    for stats in today_stats.values():
        total_trades += stats["trades"]
        total_wins += stats["wins"]
        total_losses += stats["losses"]
        total_pnl += stats["realized_pnl_thb"]

    return total_trades, total_wins, total_losses, total_pnl


def missing_position_symbols(rules: dict, active_positions: dict) -> list[str]:
    return sorted(symbol for symbol in active_positions if symbol not in rules)


def mode_notice(mode: str, active_positions: dict) -> tuple[str | None, list[str] | None]:
    if mode == "paper":
        return None, None

    if mode == "read-only":
        lines = ["Market scan and signal logging continue, but paper entries/exits are disabled."]
        if active_positions:
            lines.append(
                "Existing paper positions remain visible, but they are not managed in read-only mode."
        )
        return "Read-only mode is active", lines

    if mode == "live":
        lines = [
            "Live mode foundation is loaded for guardrail and execution testing.",
            "Strategy-driven live entry remains disconnected from the market loop.",
            "Auto live exit can be enabled separately for exchange holdings.",
            "Use hotkey M only if you intentionally want to submit the configured manual live order preset.",
        ]
        if active_positions:
            lines.append(
                "Existing paper positions remain visible, but they are not managed as live orders."
            )
        return "Live mode is in guarded foundation-only state", lines

    lines = [
        "Live execution is intentionally disabled in this build.",
        "Market scan and account snapshots can still run, but no orders or paper trades will execute.",
    ]
    if active_positions:
        lines.append(
            "Existing paper positions remain visible, but they are not managed while live-disabled mode is active."
        )
    return "Live mode is disabled in this build", lines


def execution_guardrail_message(mode: str) -> str | None:
    if mode == "read-only":
        return "Trading engine is locked by read-only mode"
    if mode == "live":
        return "Live mode selected; execution guardrails are active and strategy-driven live orders remain disconnected"
    if mode == "live-disabled":
        return "Trading engine is locked because live mode is disabled in this build"
    return None


def reconciliation_requires_safety_pause(mode: str) -> bool:
    return mode == "paper"


def cycle_report_filter(current_symbol: str | None, symbols: list[str]) -> str | None:
    ordered = [None] + sorted(symbols)
    try:
        current_index = ordered.index(current_symbol)
    except ValueError:
        current_index = 0
    return ordered[(current_index + 1) % len(ordered)]


def wait_with_hotkeys(
    seconds: int, hotkey_actions: dict[str, Callable[[], bool | None]]
) -> bool:
    end_time = time.time() + seconds

    while time.time() < end_time:
        if msvcrt.kbhit():
            key = msvcrt.getwch().lower()
            action = hotkey_actions.get(key)
            if action is not None:
                should_continue = action()
                if should_continue is False:
                    return False
        time.sleep(0.2)

    return True


def wait_for_any_key(prompt: str = "Press any key to return..."):
    print(divider("-"))
    print(prompt)
    while True:
        if msvcrt.kbhit():
            msvcrt.getwch()
            return
        time.sleep(0.1)


def confirm_action(prompt: str, details: list[str] | None = None) -> bool:
    print(divider("-"))
    print(prompt)
    for line in details or []:
        print(f"- {line}")
    print("Press Y to confirm or any other key to cancel.")
    while True:
        if msvcrt.kbhit():
            return msvcrt.getwch().lower() == "y"
        time.sleep(0.1)


def main():
    config, startup_errors = reload_config()
    if startup_errors or config is None:
        print("Config validation failed at startup:")
        for error in startup_errors:
            print(f"- {error}")
        return

    init_db()
    ensure_signal_log_file()
    ensure_trade_log_file()

    manual_pause, restore_messages = load_runtime_state(
        last_zones, positions, daily_stats, cooldowns
    )
    safety_pause = False
    safety_pause_lines: list[str] | None = None
    latest_prices: dict[str, float] = {}
    account_snapshot: dict | None = None
    report_filter_symbol: str | None = None
    last_market_cleanup_day: str | None = None
    notice: str | None = None
    notice_lines: list[str] | None = restore_messages or None
    private_api_status = "not configured"
    private_api_capabilities: list[str] | None = None
    selected_execution_order_id: int | None = None

    private_client: BitkubPrivateClient | None = None
    try:
        candidate_client = BitkubPrivateClient.from_env()
        if candidate_client.is_configured():
            private_client = candidate_client
            private_api_status = "credentials loaded"
            account_snapshot = fetch_account_snapshot(private_client)
            private_api_capabilities = summarize_account_capabilities(account_snapshot)
            snapshot_errors = account_snapshot_errors(account_snapshot)
            if snapshot_errors:
                private_api_status = "wallet/balance ready, some order endpoints unavailable"
                notice = "Private API read-only is working with limited endpoint access"
                notice_lines = (notice_lines or []) + snapshot_errors
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="private_api_status",
                    severity="warning",
                    message=notice,
                    details={
                        "status": private_api_status,
                        "errors": snapshot_errors,
                    },
                )
            else:
                private_api_status = "wallet/balance/open-orders ready"
                notice = "Private API read-only check passed"
                notice_lines = (notice_lines or []) + [
                    "Authenticated wallet/balance/open-orders snapshot fetched successfully."
                ]
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="private_api_status",
                    severity="info",
                    message=notice,
                    details={"status": private_api_status},
                )
            insert_account_snapshot(
                created_at=now_text(),
                source="startup",
                private_api_status=private_api_status,
                capabilities=private_api_capabilities,
                snapshot=account_snapshot,
            )
        else:
            private_api_status = "not configured"
            private_api_capabilities = ["wallet=OFF", "balances=OFF", "open_orders=OFF"]
    except BitkubMissingCredentialsError:
        private_api_status = "missing credentials"
        private_api_capabilities = ["wallet=OFF", "balances=OFF", "open_orders=OFF"]

    startup_mode = str(config.get("mode", "paper"))
    strategy_execution_wired = False
    startup_guardrail_message = execution_guardrail_message(startup_mode)
    if startup_guardrail_message:
        insert_runtime_event(
            created_at=now_text(),
            event_type="trading_mode",
            severity="info",
            message=startup_guardrail_message,
            details={"mode": startup_mode, "execution_enabled": False},
        )

    startup_missing_symbols = missing_position_symbols(config["rules"], positions)
    if startup_missing_symbols:
        safety_pause = True
        safety_pause_lines = [
            f"open position exists for removed symbol: {symbol}"
            for symbol in startup_missing_symbols
        ]
        safety_pause_lines.append(
            "Restore these symbols in config.json or close the positions before removing them."
        )
        notice = "Safety pause: restored positions are missing from current config"
        notice_lines = safety_pause_lines
        insert_runtime_event(
            created_at=now_text(),
            event_type="safety_pause",
            severity="warning",
            message=notice,
            details={"lines": safety_pause_lines},
        )

    startup_reconciliation_warnings = reconcile_positions_with_balances(
        positions, account_snapshot
    )
    insert_reconciliation_result(
        created_at=now_text(),
        phase="startup",
        status="warning" if startup_reconciliation_warnings else "ok",
        warnings=startup_reconciliation_warnings,
        positions_count=len(positions),
        exchange_balances=extract_available_balances(account_snapshot),
    )
    if startup_reconciliation_warnings:
        reconciliation_lines = startup_reconciliation_warnings + [
            "Read-only reconciliation detected a mismatch between local positions and exchange balances."
        ]
        if reconciliation_requires_safety_pause(startup_mode):
            safety_pause = True
            safety_pause_lines = reconciliation_lines
            notice = "Safety pause: startup reconciliation mismatch detected"
            notice_lines = safety_pause_lines
        else:
            notice = "Startup reconciliation warning detected"
            notice_lines = reconciliation_lines
        insert_runtime_event(
            created_at=now_text(),
            event_type="reconciliation",
            severity="warning",
            message=notice,
            details={
                "lines": reconciliation_lines,
                "safety_pause": reconciliation_requires_safety_pause(startup_mode),
                "mode": startup_mode,
            },
        )

    startup_execution_orders = fetch_open_execution_orders()
    if startup_execution_orders:
        startup_execution_warnings = reconcile_execution_orders_with_exchange(
            startup_execution_orders,
            account_snapshot,
            private_client,
        )
        if startup_execution_warnings:
            insert_runtime_event(
                created_at=now_text(),
                event_type="execution_reconciliation",
                severity="warning",
                message="Open execution orders require reconciliation review",
                details={"warnings": startup_execution_warnings},
            )

    def persist_state():
        save_runtime_state(
            last_zones,
            positions,
            daily_stats,
            cooldowns,
            manual_pause=manual_pause,
        )

    def run_sqlite_retention_cleanup(*, source: str, force: bool):
        nonlocal last_market_cleanup_day
        retention_days = {
            "market_snapshots": int(config["market_snapshot_retention_days"]),
            "signal_logs": int(config["signal_log_retention_days"]),
            "runtime_events": int(config["runtime_event_retention_days"]),
            "account_snapshots": int(config["account_snapshot_retention_days"]),
            "reconciliation_results": int(config["reconciliation_retention_days"]),
        }
        current_day = today_key()

        if not force and last_market_cleanup_day == current_day:
            return

        deleted_rows = prune_sqlite_retention(retention_days=retention_days)
        last_market_cleanup_day = current_day
        deleted_total = sum(deleted_rows.values())

        if deleted_total > 0 or force:
            insert_runtime_event(
                created_at=now_text(),
                event_type="sqlite_retention_cleanup",
                severity="info",
                message=f"SQLite retention cleanup completed; removed {deleted_total} total rows",
                details={
                    "source": source,
                    "deleted_rows": deleted_rows,
                    "retention_days": retention_days,
                },
            )

    run_sqlite_retention_cleanup(source="startup", force=True)
    persist_state()

    def sync_selected_execution_order():
        nonlocal selected_execution_order_id
        open_execution_orders = fetch_open_execution_orders()
        if not open_execution_orders:
            selected_execution_order_id = None
            return []

        if selected_execution_order_id is None:
            selected_execution_order_id = int(open_execution_orders[0]["id"])
            return open_execution_orders

        open_ids = {int(order["id"]) for order in open_execution_orders}
        if int(selected_execution_order_id) not in open_ids:
            selected_execution_order_id = int(open_execution_orders[0]["id"])
        return open_execution_orders

    sync_selected_execution_order()

    while True:
        try:
            rules = config["rules"]
            trading_mode = str(config.get("mode", "paper"))
            trading_enabled = trading_mode == "paper"
            live_auto_exit_enabled = bool(config.get("live_auto_exit_enabled", False))
            interval_seconds = int(config["interval_seconds"])
            fee_rate = float(config["fee_rate"])

            def activate_safety_pause(reason: str, lines: list[str], *, immediate: bool):
                nonlocal safety_pause, safety_pause_lines, notice, notice_lines
                safety_pause = True
                safety_pause_lines = list(lines)
                notice = reason
                notice_lines = safety_pause_lines
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="safety_pause",
                    severity="warning",
                    message=reason,
                    details={"lines": safety_pause_lines, "immediate": immediate},
                )
                persist_state()

                if immediate:
                    print("\n" + divider("!"))
                    print(reason.upper())
                    for line in safety_pause_lines:
                        print(f"- {line}")
                    print("Fix the issue, then press R to reload again.")
                    print(divider("!") + "\n")

            def clear_safety_pause():
                nonlocal safety_pause, safety_pause_lines
                safety_pause = False
                safety_pause_lines = None

            def reload_config_action():
                nonlocal config, notice, notice_lines, report_filter_symbol
                old_config = config
                new_config, errors = reload_config()

                if errors or new_config is None:
                    activate_safety_pause(
                        "Safety pause: invalid config.json reload was rejected",
                        errors,
                        immediate=True,
                    )
                    return True

                removed_symbols = missing_position_symbols(new_config["rules"], positions)
                if removed_symbols:
                    activate_safety_pause(
                        "Safety pause: config reload would leave open positions unmanaged",
                        [
                            f"open position still active for removed symbol: {symbol}"
                            for symbol in removed_symbols
                        ]
                        + [
                            "Reload was rejected. Restore these symbols in config.json or close the positions first."
                        ],
                        immediate=True,
                    )
                    return True

                config = new_config
                filter_was_reset = False
                if report_filter_symbol and report_filter_symbol not in new_config["rules"]:
                    report_filter_symbol = None
                    filter_was_reset = True
                change_lines = summarize_config_changes(old_config, new_config)
                if safety_pause:
                    clear_safety_pause()
                    notice = "Reloaded config.json successfully; safety pause cleared"
                else:
                    notice = "Reloaded config.json successfully"
                notice_lines = change_lines
                if filter_was_reset:
                    notice_lines = change_lines + ["report filter reset to ALL"]
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="config_reload",
                    severity="info",
                    message=notice,
                    details={"changes": change_lines},
                )
                new_mode = str(new_config.get("mode", "paper"))
                guardrail_message = execution_guardrail_message(new_mode)
                if guardrail_message:
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="trading_mode",
                        severity="info",
                        message=guardrail_message,
                        details={"mode": new_mode, "execution_enabled": False},
                    )
                run_sqlite_retention_cleanup(source="config_reload", force=True)
                persist_state()
                return True

            def toggle_pause_action():
                nonlocal manual_pause, notice, notice_lines
                if safety_pause:
                    notice = "Manual resume blocked while safety pause is active"
                    notice_lines = safety_pause_lines or [
                        "Fix the safety condition and press R to clear the safety pause."
                    ]
                    return True

                manual_pause = not manual_pause
                notice = "Manual pause enabled" if manual_pause else "Manual pause cleared"
                notice_lines = None
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="manual_pause",
                    severity="info",
                    message=notice,
                    details={"manual_pause": manual_pause},
                )
                persist_state()
                return True

            def show_positions_action():
                print_open_positions_snapshot(
                    positions=positions,
                    latest_prices=latest_prices,
                    cooldown_rows=active_cooldown_rows(),
                )
                wait_for_any_key()
                return True

            def show_daily_stats_action():
                today = today_key()
                print_daily_stats_snapshot(today=today, today_stats=daily_stats.get(today, {}))
                wait_for_any_key()
                return True

            def show_account_action():
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                if private_client is None:
                    notice = "Private API read-only is not configured"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before using account features."
                    ]
                    return True

                try:
                    account_snapshot = fetch_account_snapshot(private_client)
                except BitkubPrivateClientError as e:
                    private_api_status = "credentials loaded but account snapshot failed"
                    notice = "Account snapshot fetch failed"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="account_snapshot",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    return True

                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                if snapshot_errors:
                    private_api_status = "wallet/balance ready, some order endpoints unavailable"
                    notice = "Account snapshot fetched with limited endpoint access"
                    notice_lines = snapshot_errors
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="account_snapshot",
                        severity="warning",
                        message=notice,
                        details={"status": private_api_status, "errors": snapshot_errors},
                    )
                else:
                    private_api_status = "wallet/balance/open-orders ready"
                    notice = "Account snapshot fetched successfully"
                    notice_lines = [
                        "Wallet, balances, and open orders were refreshed from Bitkub private API."
                    ]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="account_snapshot",
                        severity="info",
                        message=notice,
                        details={"status": private_api_status},
                    )
                insert_account_snapshot(
                    created_at=now_text(),
                    source="manual_hotkey",
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )
                print_account_snapshot(account_snapshot)
                wait_for_any_key()
                return True

            def show_database_summary_action():
                summary = fetch_dashboard_summary(today=today_key())
                print_database_summary(summary, today=today_key())
                wait_for_any_key()
                return True

            def show_reporting_summary_action():
                report = fetch_reporting_summary(
                    today=today_key(), symbol=report_filter_symbol
                )
                print_reporting_summary(report, today=today_key())
                wait_for_any_key()
                return True

            def cycle_report_filter_action():
                nonlocal report_filter_symbol, notice, notice_lines
                report_filter_symbol = cycle_report_filter(
                    report_filter_symbol, list(rules.keys())
                )
                filter_label = report_filter_symbol or "ALL"
                notice = f"Report filter set to {filter_label}"
                notice_lines = [
                    "Use T to view SQLite reports for the current filter."
                ]
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="report_filter",
                    severity="info",
                    message=notice,
                    details={"symbol": report_filter_symbol},
                )
                return True

            def show_health_action():
                summary = fetch_dashboard_summary(today=today_key())
                db_storage = fetch_db_maintenance_summary()
                order_foundation = get_order_foundation_status(
                    trading_mode=trading_mode,
                    private_client=private_client,
                )
                live_execution_guardrails = build_live_execution_guardrails(
                    config=config,
                    trading_mode=trading_mode,
                    private_client=private_client,
                    private_api_capabilities=private_api_capabilities,
                    manual_pause=manual_pause,
                    safety_pause=safety_pause,
                    total_realized_pnl_thb=total_pnl,
                    available_balances=extract_available_balances(account_snapshot),
                    strategy_execution_wired=strategy_execution_wired,
                )
                live_reconciliation = summarize_live_reconciliation(
                    execution_orders=fetch_open_execution_orders(),
                    live_holdings_rows=build_live_holdings_snapshot(
                        account_snapshot=account_snapshot,
                        latest_prices=latest_prices,
                        latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
                    ),
                    account_snapshot=account_snapshot,
                    private_client=private_client,
                )
                health = {
                    "runtime_state": "SAFETY PAUSE"
                    if safety_pause
                    else "MANUAL PAUSE"
                    if manual_pause
                    else "RUNNING",
                    "trading_mode": trading_mode,
                    "execution_enabled": trading_enabled,
                    "rules_count": len(rules),
                    "open_positions": len(positions),
                    "cooldowns": len(cooldown_rows),
                    "tracked_days": len(daily_stats),
                    "config_path": str(CONFIG_PATH),
                    "state_path": str(STATE_FILE_PATH),
                    "db_path": str(DB_PATH),
                    "db_storage": db_storage,
                    "retention_days": {
                        "market_snapshots": int(config["market_snapshot_retention_days"]),
                        "signal_logs": int(config["signal_log_retention_days"]),
                        "runtime_events": int(config["runtime_event_retention_days"]),
                        "account_snapshots": int(config["account_snapshot_retention_days"]),
                        "reconciliation_results": int(
                            config["reconciliation_retention_days"]
                        ),
                    },
                    "private_api_status": private_api_status,
                    "private_api_capabilities": private_api_capabilities or [],
                    "open_orders_errors": open_orders_error_map(account_snapshot),
                    "order_foundation": order_foundation,
                    "live_execution_guardrails": live_execution_guardrails,
                    "live_reconciliation": live_reconciliation,
                    "report_filter_symbol": report_filter_symbol,
                    "latest_account_snapshot": summary.get("latest_account_snapshot"),
                    "latest_reconciliation": summary.get("latest_reconciliation"),
                    "latest_execution_order": summary.get("latest_execution_order"),
                    "notice": notice or mode_notice_text,
                    "notice_lines": notice_lines or mode_notice_lines,
                }
                print_health_snapshot(health)
                wait_for_any_key()
                return True

            def show_order_probe_action():
                nonlocal notice, notice_lines
                probe = probe_order_foundation(
                    client=private_client,
                    trading_mode=trading_mode,
                    symbols=list(rules.keys()),
                )
                notice = "Order foundation probe completed"
                notice_lines = [
                    "Probe uses read-only checks and payload preparation only.",
                    "No real order was submitted.",
                ]
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="order_probe",
                    severity="info",
                    message=notice,
                    details={
                        "status": probe.get("status"),
                        "foundation_ready": probe.get("foundation_ready"),
                        "execution_locked": probe.get("execution_locked"),
                    },
                )
                print_order_probe(probe)
                wait_for_any_key()
                return True

            def import_wallet_positions_action():
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                if safety_pause:
                    notice = "Wallet import blocked while safety pause is active"
                    notice_lines = safety_pause_lines or [
                        "Fix the safety condition and press R before importing balances into paper positions."
                    ]
                    return True

                if trading_mode != "paper":
                    notice = "Wallet import is available only in paper mode"
                    notice_lines = [
                        "Switch mode to paper in config.json, press R, then import wallet balances again.",
                        "Read-only and live-disabled modes do not manage imported paper positions.",
                    ]
                    return True

                if private_client is None:
                    notice = "Wallet import requires private API credentials"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before importing wallet balances."
                    ]
                    return True

                try:
                    account_snapshot = fetch_account_snapshot(private_client)
                except BitkubPrivateClientError as e:
                    notice = "Wallet import failed while refreshing account snapshot"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="wallet_import",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    return True

                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                if snapshot_errors:
                    private_api_status = "wallet/balance ready, some order endpoints unavailable"
                else:
                    private_api_status = "wallet/balance/open-orders ready"
                insert_account_snapshot(
                    created_at=now_text(),
                    source="wallet_import_hotkey",
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )

                available_balances = extract_available_balances(account_snapshot)
                if not available_balances:
                    notice = "Wallet import failed because no readable balances were available"
                    notice_lines = [
                        "Refresh account access with A and confirm balances=OK before importing.",
                    ]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="wallet_import",
                        severity="warning",
                        message=notice,
                        details={"status": private_api_status},
                    )
                    return True

                try:
                    ticker = get_ticker()
                except Exception as e:
                    notice = "Wallet import failed while fetching latest ticker prices"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="wallet_import",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    return True

                import_timestamp = now_text()
                imported_lines: list[str] = []
                skipped_lines: list[str] = []

                for symbol, rule in sorted(rules.items()):
                    asset = symbol_to_asset(symbol)
                    available_qty = float(available_balances.get(asset, 0.0))

                    if available_qty <= 0:
                        continue

                    if symbol in positions:
                        skipped_lines.append(
                            f"{symbol}: skipped because a paper position already exists"
                        )
                        continue

                    ticker_entry = ticker.get(symbol)
                    if not isinstance(ticker_entry, dict) or "last" not in ticker_entry:
                        skipped_lines.append(
                            f"{symbol}: skipped because latest ticker price is unavailable"
                        )
                        continue

                    last_price = float(ticker_entry["last"])
                    if last_price <= 0:
                        skipped_lines.append(
                            f"{symbol}: skipped because latest ticker price is invalid"
                        )
                        continue

                    import_wallet_position(
                        symbol,
                        last_price=last_price,
                        coin_qty=available_qty,
                        config=rule,
                        positions=positions,
                        timestamp=import_timestamp,
                    )
                    latest_prices[symbol] = last_price
                    imported_lines.append(
                        f"{symbol}: imported qty={available_qty:,.8f} at price={last_price:,.8f}"
                    )

                if imported_lines:
                    notice = "Imported wallet balances into paper positions"
                    notice_lines = imported_lines + skipped_lines
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="wallet_import",
                        severity="info",
                        message=notice,
                        details={
                            "imported": imported_lines,
                            "skipped": skipped_lines,
                        },
                    )
                else:
                    notice = "No wallet balances were imported into paper positions"
                    notice_lines = skipped_lines or [
                        "No configured symbols had a positive available balance in the wallet.",
                    ]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="wallet_import",
                        severity="warning",
                        message=notice,
                        details={"skipped": skipped_lines},
                    )

                persist_state()
                return True

            def clear_local_paper_positions_action():
                nonlocal notice, notice_lines
                if not positions:
                    notice = "No local paper positions were cleared"
                    notice_lines = ["The runtime state does not currently contain any open paper positions."]
                    return True

                removed_symbols = sorted(positions)
                positions.clear()
                for symbol in removed_symbols:
                    cooldowns.pop(symbol, None)
                    latest_prices.pop(symbol, None)

                notice = "Local paper positions were cleared"
                notice_lines = [
                    f"removed symbols: {', '.join(removed_symbols)}",
                    "This only affects local paper state; no real Bitkub orders or balances were changed.",
                ]
                if safety_pause:
                    notice_lines.append(
                        "Press R to refresh reconciliation and clear the safety pause if no other issue remains."
                    )

                insert_runtime_event(
                    created_at=now_text(),
                    event_type="clear_paper_positions",
                    severity="warning",
                    message=notice,
                    details={"removed_symbols": removed_symbols},
                )
                persist_state()
                return True

            def submit_manual_live_order_action():
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                if private_client is None:
                    notice = "Manual live order requires private API credentials"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before using live order features."
                    ]
                    return True

                try:
                    account_snapshot = fetch_account_snapshot(private_client)
                except BitkubPrivateClientError as e:
                    notice = "Manual live order failed while refreshing account snapshot"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="manual_live_order",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    return True

                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                private_api_status = (
                    "wallet/balance ready, some order endpoints unavailable"
                    if snapshot_errors
                    else "wallet/balance/open-orders ready"
                )
                insert_account_snapshot(
                    created_at=now_text(),
                    source="manual_live_order_hotkey",
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )

                live_guardrails = build_live_execution_guardrails(
                    config=config,
                    trading_mode=trading_mode,
                    private_client=private_client,
                    private_api_capabilities=private_api_capabilities,
                    manual_pause=manual_pause,
                    safety_pause=safety_pause,
                    total_realized_pnl_thb=total_pnl,
                    available_balances=extract_available_balances(account_snapshot),
                    strategy_execution_wired=strategy_execution_wired,
                )

                manual_order = dict(config.get("live_manual_order", {}))
                if not confirm_action(
                    "Confirm manual live order?",
                    [
                        f"symbol={manual_order.get('symbol')}",
                        f"side={manual_order.get('side')}",
                        f"order_type={manual_order.get('order_type')}",
                        f"amount_thb={manual_order.get('amount_thb')}",
                        f"amount_coin={manual_order.get('amount_coin')}",
                        f"rate={manual_order.get('rate')}",
                    ],
                ):
                    notice = "Manual live order canceled by user"
                    notice_lines = ["No real order was submitted."]
                    return True

                try:
                    order_record, order_events = submit_manual_live_order(
                        client=private_client,
                        config=config,
                        rules=rules,
                        guardrails=live_guardrails,
                        available_balances=extract_available_balances(account_snapshot),
                        created_at=now_text(),
                    )
                except LiveExecutionGuardrailError as e:
                    notice = "Manual live order blocked by guardrails"
                    notice_lines = str(e).split("; ")
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="manual_live_order",
                        severity="warning",
                        message=notice,
                        details={"errors": notice_lines, "guardrails": live_guardrails},
                    )
                    return True
                except Exception as e:
                    notice = "Manual live order submission failed"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="manual_live_order",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    return True

                execution_order_id = insert_execution_order(
                    created_at=order_record["created_at"],
                    updated_at=order_record["updated_at"],
                    symbol=order_record["symbol"],
                    side=order_record["side"],
                    order_type=order_record["order_type"],
                    state=order_record["state"],
                    request_payload=order_record["request_payload"],
                    response_payload=order_record.get("response_payload"),
                    guardrails=order_record.get("guardrails"),
                    exchange_order_id=order_record.get("exchange_order_id"),
                    exchange_client_id=order_record.get("exchange_client_id"),
                    message=order_record["message"],
                )
                update_execution_order(
                    execution_order_id=execution_order_id,
                    updated_at=order_record["updated_at"],
                    state=order_record["state"],
                    response_payload=order_record.get("response_payload"),
                    exchange_order_id=order_record.get("exchange_order_id"),
                    exchange_client_id=order_record.get("exchange_client_id"),
                    message=order_record["message"],
                )
                for event in order_events:
                    insert_execution_order_event(
                        execution_order_id=execution_order_id,
                        created_at=event["created_at"],
                        from_state=event["from_state"],
                        to_state=event["to_state"],
                        event_type=event["event_type"],
                        message=event["message"],
                        details=event.get("details"),
                    )

                notice = "Manual live order submitted"
                notice_lines = [
                    f"id={execution_order_id} symbol={order_record['symbol']} side={order_record['side']} state={order_record['state']}",
                ]
                if order_record.get("exchange_order_id"):
                    notice_lines.append(
                        f"exchange_order_id={order_record['exchange_order_id']}"
                    )
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="manual_live_order",
                    severity="warning",
                    message=notice,
                    details={
                        "execution_order_id": execution_order_id,
                        "symbol": order_record["symbol"],
                        "side": order_record["side"],
                        "state": order_record["state"],
                    },
                )
                sync_selected_execution_order()
                return True

            def persist_execution_order_changes(
                execution_order_id: int,
                order_record: dict,
                order_events: list[dict],
            ):
                update_execution_order(
                    execution_order_id=execution_order_id,
                    updated_at=order_record["updated_at"],
                    state=order_record["state"],
                    response_payload=order_record.get("response_payload"),
                    exchange_order_id=order_record.get("exchange_order_id"),
                    exchange_client_id=order_record.get("exchange_client_id"),
                    message=order_record["message"],
                )
                for event in order_events:
                    insert_execution_order_event(
                        execution_order_id=execution_order_id,
                        created_at=event["created_at"],
                        from_state=event["from_state"],
                        to_state=event["to_state"],
                        event_type=event["event_type"],
                        message=event["message"],
                        details=event.get("details"),
                    )

            def submit_auto_live_exit_action(candidate: dict) -> bool:
                nonlocal account_snapshot, notice, notice_lines
                if private_client is None:
                    return False

                available_balances = extract_available_balances(account_snapshot)
                live_guardrails = build_live_execution_guardrails(
                    config=config,
                    trading_mode=trading_mode,
                    private_client=private_client,
                    private_api_capabilities=private_api_capabilities,
                    manual_pause=manual_pause,
                    safety_pause=safety_pause,
                    total_realized_pnl_thb=total_pnl,
                    available_balances=available_balances,
                    strategy_execution_wired=strategy_execution_wired,
                )

                try:
                    order_record, order_events = submit_auto_live_exit_order(
                        client=private_client,
                        symbol=str(candidate["symbol"]),
                        amount_coin=float(candidate["amount_coin"]),
                        rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        exit_reason=str(candidate["exit_reason"]),
                        guardrails=live_guardrails,
                        available_balances=available_balances,
                        created_at=now_text(),
                    )
                except LiveExecutionGuardrailError as e:
                    notice = "Auto live exit blocked by guardrails"
                    notice_lines = str(e).split("; ")
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="auto_live_exit",
                        severity="warning",
                        message=notice,
                        details={
                            "symbol": candidate["symbol"],
                            "exit_reason": candidate["exit_reason"],
                            "errors": notice_lines,
                        },
                    )
                    return False
                except Exception as e:
                    notice = "Auto live exit submission failed"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="auto_live_exit",
                        severity="error",
                        message=notice,
                        details={
                            "symbol": candidate["symbol"],
                            "exit_reason": candidate["exit_reason"],
                            "errors": notice_lines,
                        },
                    )
                    return False

                execution_order_id = insert_execution_order(
                    created_at=order_record["created_at"],
                    updated_at=order_record["updated_at"],
                    symbol=order_record["symbol"],
                    side=order_record["side"],
                    order_type=order_record["order_type"],
                    state=order_record["state"],
                    request_payload=order_record["request_payload"],
                    response_payload=order_record.get("response_payload"),
                    guardrails=order_record.get("guardrails"),
                    exchange_order_id=order_record.get("exchange_order_id"),
                    exchange_client_id=order_record.get("exchange_client_id"),
                    message=order_record["message"],
                )
                persist_execution_order_changes(
                    execution_order_id=execution_order_id,
                    order_record=order_record,
                    order_events=order_events,
                )

                notice = "Auto live exit order submitted"
                notice_lines = [
                    f"id={execution_order_id} symbol={order_record['symbol']} reason={candidate['exit_reason']} state={order_record['state']}",
                    f"amount={float(candidate['amount_coin']):,.8f} rate={float(candidate['rate']):,.8f} latest={float(candidate['latest_price']):,.8f}",
                ]
                if order_record.get("exchange_order_id"):
                    notice_lines.append(
                        f"exchange_order_id={order_record['exchange_order_id']}"
                    )
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="auto_live_exit",
                    severity="warning",
                    message=notice,
                    details={
                        "execution_order_id": execution_order_id,
                        "symbol": order_record["symbol"],
                        "state": order_record["state"],
                        "exit_reason": candidate["exit_reason"],
                    },
                )
                return True

            def refresh_live_orders_action():
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                if private_client is None:
                    notice = "Live order refresh requires private API credentials"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before using live order controls."
                    ]
                    return True

                open_execution_orders = fetch_open_execution_orders()
                if not open_execution_orders:
                    notice = "No open live orders were found in SQLite"
                    notice_lines = [
                        "There are no non-terminal execution_orders to refresh right now."
                    ]
                    return True

                try:
                    account_snapshot = fetch_account_snapshot(private_client)
                except BitkubPrivateClientError as e:
                    notice = "Live order refresh failed while refreshing account snapshot"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="live_order_refresh",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    return True

                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                private_api_status = (
                    "wallet/balance ready, some order endpoints unavailable"
                    if snapshot_errors
                    else "wallet/balance/open-orders ready"
                )
                insert_account_snapshot(
                    created_at=now_text(),
                    source="live_order_refresh_hotkey",
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )

                refreshed_lines: list[str] = []
                for open_order in open_execution_orders:
                    execution_order_id = int(open_order["id"])
                    try:
                        refreshed_record, refresh_events = refresh_live_order_from_exchange(
                            client=private_client,
                            order_record=open_order,
                            occurred_at=now_text(),
                        )
                    except Exception as e:
                        refreshed_lines.append(
                            f"id={execution_order_id} symbol={open_order['symbol']} refresh failed: {e}"
                        )
                        continue

                    persist_execution_order_changes(
                        execution_order_id=execution_order_id,
                        order_record=refreshed_record,
                        order_events=refresh_events,
                    )
                    refreshed_lines.append(
                        f"id={execution_order_id} symbol={refreshed_record['symbol']} side={refreshed_record['side']} state={refreshed_record['state']}"
                    )

                notice = "Live order status refresh completed"
                notice_lines = refreshed_lines
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="live_order_refresh",
                    severity="info",
                    message=notice,
                    details={"orders": refreshed_lines},
                )
                sync_selected_execution_order()
                return True

            def cancel_live_order_action():
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                if private_client is None:
                    notice = "Live order cancel requires private API credentials"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before using live order controls."
                    ]
                    return True

                open_execution_orders = sync_selected_execution_order()
                if not open_execution_orders:
                    notice = "No open live orders were found to cancel"
                    notice_lines = [
                        "There are no non-terminal execution_orders recorded in SQLite."
                    ]
                    return True

                target_order = next(
                    (
                        order
                        for order in open_execution_orders
                        if int(order["id"]) == int(selected_execution_order_id or -1)
                    ),
                    open_execution_orders[0],
                )
                execution_order_id = int(target_order["id"])

                if not confirm_action(
                    "Confirm live order cancel?",
                    [
                        f"id={execution_order_id}",
                        f"symbol={target_order['symbol']}",
                        f"side={target_order['side']}",
                        f"state={target_order['state']}",
                        f"exchange_order_id={target_order.get('exchange_order_id')}",
                    ],
                ):
                    notice = "Live order cancel canceled by user"
                    notice_lines = [f"id={execution_order_id} was not canceled."]
                    return True

                try:
                    account_snapshot = fetch_account_snapshot(private_client)
                except BitkubPrivateClientError as e:
                    notice = "Live order cancel failed while refreshing account snapshot"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="live_order_cancel",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    return True

                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                private_api_status = (
                    "wallet/balance ready, some order endpoints unavailable"
                    if snapshot_errors
                    else "wallet/balance/open-orders ready"
                )
                insert_account_snapshot(
                    created_at=now_text(),
                    source="live_order_cancel_hotkey",
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )

                try:
                    canceled_record, cancel_events = cancel_live_order(
                        client=private_client,
                        order_record=target_order,
                        occurred_at=now_text(),
                    )
                except Exception as e:
                    notice = "Live order cancel failed"
                    notice_lines = [str(e)]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="live_order_cancel",
                        severity="error",
                        message=notice,
                        details={
                            "execution_order_id": execution_order_id,
                            "errors": notice_lines,
                        },
                    )
                    return True

                persist_execution_order_changes(
                    execution_order_id=execution_order_id,
                    order_record=canceled_record,
                    order_events=cancel_events,
                )
                notice = "Live order cancel request completed"
                notice_lines = [
                    f"id={execution_order_id} symbol={canceled_record['symbol']} side={canceled_record['side']} state={canceled_record['state']}"
                ]
                if canceled_record.get("exchange_order_id"):
                    notice_lines.append(
                        f"exchange_order_id={canceled_record['exchange_order_id']}"
                    )
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="live_order_cancel",
                    severity="warning",
                    message=notice,
                    details={
                        "execution_order_id": execution_order_id,
                        "symbol": canceled_record["symbol"],
                        "side": canceled_record["side"],
                        "state": canceled_record["state"],
                    },
                )
                sync_selected_execution_order()
                return True

            def cycle_execution_order_action():
                nonlocal selected_execution_order_id, notice, notice_lines
                open_execution_orders = sync_selected_execution_order()
                if not open_execution_orders:
                    notice = "No open execution orders to select"
                    notice_lines = ["Use M to submit an order or wait until a live order is open."]
                    return True

                ordered_ids = [int(order["id"]) for order in open_execution_orders]
                current_id = int(selected_execution_order_id or ordered_ids[0])
                try:
                    current_index = ordered_ids.index(current_id)
                except ValueError:
                    current_index = 0
                selected_execution_order_id = ordered_ids[(current_index + 1) % len(ordered_ids)]
                selected_order = next(
                    order for order in open_execution_orders if int(order["id"]) == selected_execution_order_id
                )
                notice = "Selected live order target updated"
                notice_lines = [
                    f"id={selected_order['id']} symbol={selected_order['symbol']} side={selected_order['side']} state={selected_order['state']}"
                ]
                return True

            def show_execution_orders_action():
                open_execution_orders = sync_selected_execution_order()
                if not open_execution_orders:
                    selected_id = None
                else:
                    selected_id = selected_execution_order_id
                execution_summary = fetch_execution_console_summary()
                execution_summary["live_reconciliation"] = summarize_live_reconciliation(
                    execution_orders=execution_summary.get("open_orders", []),
                    live_holdings_rows=build_live_holdings_snapshot(
                        account_snapshot=account_snapshot,
                        latest_prices=latest_prices,
                        latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
                    ),
                    account_snapshot=account_snapshot,
                    private_client=private_client,
                )
                print_execution_orders_snapshot(execution_summary, selected_id)
                wait_for_any_key()
                return True

            def show_live_holdings_action():
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                if private_client is None:
                    notice = "Live holdings require private API credentials"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before using live holdings.",
                    ]
                    return True

                try:
                    account_snapshot = fetch_account_snapshot(private_client)
                except BitkubPrivateClientError as e:
                    notice = "Live holdings refresh failed"
                    notice_lines = [str(e)]
                    return True

                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                private_api_status = (
                    "wallet/balance ready, some order endpoints unavailable"
                    if snapshot_errors
                    else "wallet/balance/open-orders ready"
                )
                insert_account_snapshot(
                    created_at=now_text(),
                    source="live_holdings_hotkey",
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )

                live_holdings_rows = build_live_holdings_snapshot(
                    account_snapshot=account_snapshot,
                    latest_prices=latest_prices,
                    latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
                )
                print_live_holdings_snapshot(live_holdings_rows)
                wait_for_any_key()
                return True

            def quit_action():
                persist_state()
                print("\n[SYSTEM] Exiting bot\n")
                return False

            hotkey_actions = {
                "r": reload_config_action,
                "p": toggle_pause_action,
                "s": show_positions_action,
                "d": show_daily_stats_action,
                "a": show_account_action,
                "b": show_database_summary_action,
                "t": show_reporting_summary_action,
                "f": cycle_report_filter_action,
                "h": show_health_action,
                "o": show_order_probe_action,
                "i": import_wallet_positions_action,
                "c": clear_local_paper_positions_action,
                "m": submit_manual_live_order_action,
                "l": show_live_holdings_action,
                "e": show_execution_orders_action,
                "k": cycle_execution_order_action,
                "u": refresh_live_orders_action,
                "x": cancel_live_order_action,
                "q": quit_action,
            }

            cooldown_rows = active_cooldown_rows()
            total_trades, total_wins, total_losses, total_pnl = daily_totals()
            timestamp = now_text()
            mode_notice_text, mode_notice_lines = mode_notice(trading_mode, positions)
            render_header(
                timestamp=timestamp,
                trading_mode=trading_mode,
                fee_rate=fee_rate,
                interval_seconds=interval_seconds,
                manual_pause=manual_pause,
                safety_pause=safety_pause,
                notice=notice or mode_notice_text,
                notice_lines=notice_lines or mode_notice_lines,
                open_positions_count=len(positions),
                tracked_symbols_today=len(daily_stats.get(today_key(), {})),
                active_cooldowns_count=len(cooldown_rows),
                total_trades=total_trades,
                total_wins=total_wins,
                total_losses=total_losses,
                total_pnl=total_pnl,
                private_api_status=private_api_status,
                private_api_capabilities=private_api_capabilities,
            )
            notice = None
            notice_lines = None

            if manual_pause or safety_pause:
                run_sqlite_retention_cleanup(source="paused_loop", force=False)
                section_title("SYSTEM STATUS")

                if safety_pause:
                    print("Trading loop is safety-paused.")
                    print("No new market checks or entries will run until the safety condition is cleared.")
                    print("Press R after fixing the issue. P cannot clear a safety pause.")
                    if safety_pause_lines:
                        print(divider("-"))
                        for line in safety_pause_lines:
                            print(f"- {line}")
                else:
                    print("Trading loop is manually paused.")
                    print("Prices and signals will not update until you press P again.")

                print(divider("-"))
                print(
                    f"Waiting {interval_seconds}s for hotkeys... "
                    "(R=reload, P=manual pause/resume, S=positions, D=daily stats, "
                    "A=account, B=db summary, T=reports, F=report filter, H=health, "
                    "O=orders, I=wallet import, C=clear paper, M=manual live, "
                    "L=live holdings, E=execution, K=next order, U=update live, X=cancel live, Q=quit)"
                )

                should_continue = wait_with_hotkeys(interval_seconds, hotkey_actions)
                if not should_continue:
                    break
                continue

            ticker = get_ticker()
            run_sqlite_retention_cleanup(source="market_loop", force=False)
            market_rows: list[dict] = []

            for symbol, rule in rules.items():
                if symbol not in ticker:
                    latest_prices.pop(symbol, None)
                    market_rows.append(
                        {
                            "symbol": symbol,
                            "last_text": "n/a",
                            "buy_below": f"{float(rule['buy_below']):,.8f}",
                            "sell_above": f"{float(rule['sell_above']):,.8f}",
                            "zone": "-",
                            "status": "Ticker data unavailable",
                            "detail": "position: unavailable because latest market price was not returned",
                        }
                    )
                    continue

                last_price = float(ticker[symbol]["last"])
                latest_prices[symbol] = last_price

                buy_below = float(rule["buy_below"])
                sell_above = float(rule["sell_above"])

                current_zone = get_zone(last_price, buy_below, sell_above)
                prev_zone = last_zones.get(symbol)
                changed = zone_changed(prev_zone, current_zone)

                if changed:
                    if current_zone == "BUY":
                        status = "BUY SIGNAL"
                        signal_message = "BUY SIGNAL: price entered buy zone"
                    elif current_zone == "SELL":
                        status = "SELL SIGNAL"
                        signal_message = "SELL SIGNAL: price entered sell zone"
                    else:
                        status = "BACK TO WAIT"
                        signal_message = "WAIT: price returned to mid zone"

                    write_signal_log(
                        timestamp,
                        symbol,
                        last_price,
                        buy_below,
                        sell_above,
                        current_zone,
                        signal_message,
                    )
                    last_zones[symbol] = current_zone
                else:
                    if current_zone == "BUY":
                        status = "BUY ZONE"
                    elif current_zone == "SELL":
                        status = "SELL ZONE"
                    else:
                        status = "WAIT ZONE"

                if trading_enabled:
                    handle_symbol(
                        symbol=symbol,
                        zone=current_zone,
                        zone_changed_flag=changed,
                        last_price=last_price,
                        config=rule,
                        positions=positions,
                        daily_stats=daily_stats,
                        cooldowns=cooldowns,
                        timestamp=timestamp,
                    )

                insert_market_snapshot(
                    created_at=timestamp,
                    symbol=symbol,
                    last_price=last_price,
                    buy_below=buy_below,
                    sell_above=sell_above,
                    zone=current_zone,
                    status=status,
                    trading_mode=trading_mode,
                )

                market_rows.append(
                    {
                        "symbol": symbol,
                        "last_text": f"{last_price:,.8f}",
                        "buy_below": f"{buy_below:,.8f}",
                        "sell_above": f"{sell_above:,.8f}",
                        "zone": current_zone,
                        "status": status,
                        "detail": position_detail_text(
                            symbol=symbol,
                            last_price=last_price,
                            rule=rule,
                            fee_rate=fee_rate,
                            positions=positions,
                        ),
                    }
                )

            print_market_table(market_rows)
            if not trading_enabled:
                if trading_mode == "live":
                    if live_auto_exit_enabled:
                        print(
                            "Live foundation active: strategy-driven live entries are disconnected, but auto live exit is enabled."
                        )
                        print(
                            "Market scan continues and exchange holdings may place guarded sell orders when exit conditions trigger."
                        )
                    else:
                        print(
                            "Live foundation active: strategy-driven live execution is not wired into the market loop yet."
                        )
                        print(
                            "Market scan continues, but no real orders are submitted in this build."
                        )
                else:
                    print(
                        "Trading engine locked: "
                        f"{trading_mode} mode disables paper entries and exits in this build."
                    )
                if positions:
                    print(
                        "Open paper positions are shown for visibility only and are not being managed."
                    )

            cooldown_rows = active_cooldown_rows()
            total_trades, total_wins, total_losses, total_pnl = daily_totals()
            print(
                f"Loop summary: {len(market_rows)} symbols | "
                f"{len(positions)} open positions | "
                f"{len(cooldown_rows)} active cooldowns"
            )
            print(
                f"Daily snapshot: trades={total_trades} wins={total_wins} "
                f"losses={total_losses} realized={total_pnl:,.2f} THB"
            )

            if trading_mode == "live" and private_client is not None:
                open_execution_orders = fetch_open_execution_orders()
                for open_order in open_execution_orders:
                    execution_order_id = int(open_order["id"])
                    try:
                        refreshed_record, refresh_events = refresh_live_order_from_exchange(
                            client=private_client,
                            order_record=open_order,
                            occurred_at=now_text(),
                        )
                    except Exception:
                        continue

                    if (
                        refreshed_record["state"] != open_order["state"]
                        or refreshed_record["updated_at"] != open_order["updated_at"]
                    ):
                        persist_execution_order_changes(
                            execution_order_id=execution_order_id,
                            order_record=refreshed_record,
                            order_events=refresh_events,
                        )

                if live_auto_exit_enabled and not manual_pause and not safety_pause:
                    try:
                        account_snapshot = fetch_account_snapshot(private_client)
                        snapshot_errors = account_snapshot_errors(account_snapshot)
                        private_api_capabilities = summarize_account_capabilities(
                            account_snapshot
                        )
                        private_api_status = (
                            "wallet/balance ready, some order endpoints unavailable"
                            if snapshot_errors
                            else "wallet/balance/open-orders ready"
                        )
                        latest_filled_orders = fetch_latest_filled_execution_orders_by_symbol()
                        live_holdings_rows = build_live_holdings_snapshot(
                            account_snapshot=account_snapshot,
                            latest_prices=latest_prices,
                            latest_filled_execution_orders=latest_filled_orders,
                        )
                        exit_candidates = evaluate_live_exit_candidates(
                            rules=rules,
                            live_holdings_rows=live_holdings_rows,
                            open_execution_orders=fetch_open_execution_orders(),
                            exchange_open_orders_by_symbol=extract_open_orders_by_symbol(
                                account_snapshot
                            ),
                        )
                        if exit_candidates:
                            submit_auto_live_exit_action(exit_candidates[0])
                    except Exception as e:
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type="auto_live_exit",
                            severity="error",
                            message="Auto live exit evaluation failed",
                            details={"exception": str(e)},
                        )

            print(
                f"Waiting {interval_seconds}s for next refresh... "
                "(R=reload, P=manual pause, S=positions, D=daily stats, "
                "A=account, B=db summary, T=reports, F=report filter, H=health, "
                "O=orders, I=wallet import, C=clear paper, M=manual live, "
                "L=live holdings, E=execution, K=next order, U=update live, X=cancel live, Q=quit)"
            )

            persist_state()

            should_continue = wait_with_hotkeys(interval_seconds, hotkey_actions)
            if not should_continue:
                break

        except KeyboardInterrupt:
            persist_state()
            print("\nStopped by user")
            break
        except Exception as e:
            persist_state()
            notice = f"Runtime error: {e}"
            insert_runtime_event(
                created_at=now_text(),
                event_type="runtime_error",
                severity="error",
                message=notice,
                details={"exception": str(e)},
            )
            print(f"\nRuntime error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
