import secrets
import time
from datetime import timedelta
from collections.abc import Callable
from typing import Any

try:
    import msvcrt
except ImportError:  # pragma: no cover - Linux/VPS fallback
    msvcrt = None

from clients.bitkub_client import get_ticker
from clients.bitkub_private_client import (
    BitkubMissingCredentialsError,
    BitkubPrivateClient,
    BitkubPrivateClientError,
    is_unsupported_symbol_error_message,
)
from config import (
    CONFIG_PATH,
    ordered_unique_symbols,
    reload_config,
    save_config,
    summarize_config_changes,
)
from core.strategy import get_zone, zone_changed
from core.trade_engine import handle_symbol, import_wallet_position
from services.account_service import (
    account_snapshot_errors,
    build_live_holdings_snapshot,
    fetch_account_snapshot,
    open_orders_error_map,
    summarize_account_capabilities,
    unsupported_open_orders_symbol_map,
)
from services.db_service import (
    DB_PATH,
    fetch_db_maintenance_summary,
    fetch_dashboard_summary,
    fetch_execution_console_summary,
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_reporting_summary,
    fetch_recent_telegram_command_log,
    fetch_runtime_event_log,
    expire_stale_telegram_command_logs,
    init_db,
    insert_account_snapshot,
    insert_execution_order,
    insert_execution_order_event,
    insert_market_snapshot,
    insert_reconciliation_result,
    insert_state_reconciliation_run,
    insert_trade_journal,
    insert_telegram_command_log,
    insert_runtime_event,
    cleanup_sqlite_retention,
    update_telegram_command_log,
    update_execution_order,
)
from services.execution_service import (
    LiveExecutionGuardrailError,
    build_live_execution_guardrails,
    build_live_buy_request,
    build_live_sell_request,
    build_manual_live_order_request,
    cancel_live_order,
    evaluate_live_entry_candidates,
    evaluate_live_exit_candidates,
    refresh_live_order_from_exchange,
    submit_auto_live_entry_order,
    submit_auto_live_exit_order,
    submit_manual_live_order,
    validate_live_buy_request_guardrails,
    validate_live_sell_request_guardrails,
    validate_manual_live_order_guardrails,
)
from services.audit_service import (
    audit_config_change,
    audit_event,
    new_correlation_id,
)
from services.log_service import (
    ensure_signal_log_file,
    ensure_trade_log_file,
    write_signal_log,
)
from services.market_symbol_service import (
    build_non_exchange_symbol_source_map,
    fetch_market_symbol_directory,
)
from services.order_service import get_order_foundation_status, probe_order_foundation
from services.reconciliation_service import (
    extract_available_balances,
    collect_runtime_reconciliation_findings,
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
from services.telegram_service import (
    fetch_telegram_command_updates,
    flush_telegram_outbox,
    queue_telegram_notification,
    send_telegram_direct_message,
    telegram_chat_is_authorized,
    telegram_settings_snapshot,
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
from services.version_service import (
    format_app_version_detail,
    format_app_version_label,
    get_app_version_snapshot,
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


def build_telegram_position_line(
    *,
    symbol: str,
    latest_prices: dict[str, float],
    rules: dict[str, dict[str, Any]],
    fee_rate: float,
    positions: dict[str, dict[str, Any]],
) -> str:
    position = positions.get(symbol, {})
    fallback_last_price = float(position.get("buy_price", 0.0) or 0.0)
    last_price = float(latest_prices.get(symbol, fallback_last_price) or fallback_last_price)
    rule = dict(rules.get(symbol, {}))
    if "sell_above" not in rule:
        rule["sell_above"] = float(position.get("sell_above", last_price) or last_price)
    if "stop_loss_percent" not in rule:
        rule["stop_loss_percent"] = float(position.get("stop_loss_percent", 0.0) or 0.0)
    if "take_profit_percent" not in rule:
        rule["take_profit_percent"] = float(position.get("take_profit_percent", 0.0) or 0.0)

    return f"{symbol}: {position_detail_text(symbol, last_price, rule, fee_rate, positions)}"


def should_queue_config_reload_telegram_notification(*, source: str) -> bool:
    normalized_source = str(source or "").strip().lower()
    return normalized_source not in {"telegram", "telegram_confirmation"}


def should_queue_safety_pause_telegram_notification(*, source: str) -> bool:
    normalized_source = str(source or "").strip().lower()
    return normalized_source not in {"telegram", "telegram_confirmation"}


def audit_actor_type_from_source(source: str) -> str:
    normalized_source = str(source or "").strip().lower()
    if normalized_source.startswith("telegram"):
        return "telegram"
    if normalized_source in {"ui", "streamlit_ui"}:
        return "ui"
    if "hotkey" in normalized_source or normalized_source in {"console", "operator", "manual"}:
        return "manual"
    return "system"


def audit_runtime_mode_change(
    *,
    old_config: dict[str, Any] | None,
    new_config: dict[str, Any],
    actor_type: str,
    source: str,
    message: str,
    actor_id: str | None = None,
    correlation_id: str | None = None,
) -> None:
    if old_config is None:
        old_state = None
    else:
        old_state = {
            "mode": old_config.get("mode"),
            "live_execution_enabled": bool(old_config.get("live_execution_enabled", False)),
            "live_auto_entry_enabled": bool(old_config.get("live_auto_entry_enabled", False)),
            "live_auto_exit_enabled": bool(old_config.get("live_auto_exit_enabled", False)),
        }
    new_state = {
        "mode": new_config.get("mode"),
        "live_execution_enabled": bool(new_config.get("live_execution_enabled", False)),
        "live_auto_entry_enabled": bool(new_config.get("live_auto_entry_enabled", False)),
        "live_auto_exit_enabled": bool(new_config.get("live_auto_exit_enabled", False)),
    }
    if old_state == new_state:
        return

    audit_event(
        action_type="mode_change",
        actor_type=actor_type,
        actor_id=actor_id,
        source=source,
        target_type="runtime_mode",
        target_id="engine",
        old_value=old_state,
        new_value=new_state,
        status="succeeded",
        message=message,
        correlation_id=correlation_id,
    )


def _unsupported_live_entry_reason(*, source: str, error_message: str | None = None) -> str:
    normalized_source = str(source or "").strip().lower()
    normalized_error = str(error_message or "").strip()
    if normalized_source == "market_source":
        market_source = normalized_error or "unknown"
        return (
            "symbol is blocked because Bitkub market metadata marks it as "
            f"source={market_source}, not source=exchange, so the bot will not use it for live auto entry"
        )
    if normalized_source == "open_orders_probe":
        return (
            "symbol is blocked because Bitkub rejected open_orders for it, "
            "so the bot cannot safely track live order state for this symbol"
        )
    if normalized_error:
        return (
            "symbol is blocked because Bitkub rejected a live order request for it: "
            + normalized_error
        )
    return "symbol is blocked because Bitkub rejected a live order request for it"


def _is_market_source_block_reason(reason: str | None) -> bool:
    return "market metadata marks it as source=" in str(reason or "").lower()


def missing_position_symbols(rules: dict, active_positions: dict) -> list[str]:
    return sorted(symbol for symbol in active_positions if symbol not in rules)


def missing_position_tracking_label(mode: str) -> str:
    normalized_mode = str(mode or "").strip().lower() or "unknown"
    if normalized_mode == "paper":
        return "mode=paper, local paper position"
    return f"mode={normalized_mode}, local paper position shown for visibility"


def describe_missing_position_line(
    *,
    prefix: str,
    symbol: str,
    position: dict,
    mode: str,
) -> str:
    try:
        qty = float(position.get("coin_qty", 0.0) or 0.0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        buy_price = float(position.get("buy_price", 0.0) or 0.0)
    except (TypeError, ValueError):
        buy_price = 0.0
    try:
        budget = float(position.get("budget_thb", 0.0) or 0.0)
    except (TypeError, ValueError):
        budget = 0.0
    buy_time = str(position.get("buy_time", "") or "").strip() or "unknown"
    entry_source = str(position.get("entry_source", "") or "").strip() or "unknown"
    tracking = missing_position_tracking_label(mode)
    return (
        f"{prefix} {symbol} "
        f"({tracking}, qty={qty:.8f}, buy_price={buy_price:.8f}, "
        f"budget_thb={budget:.2f}, buy_time={buy_time}, entry_source={entry_source})"
    )


def build_missing_position_block_lines(
    *,
    prefix: str,
    removed_symbols: list[str],
    active_positions: dict,
    mode: str,
    closing_note: str,
) -> list[str]:
    lines = [
        describe_missing_position_line(
            prefix=prefix,
            symbol=symbol,
            position=active_positions.get(symbol, {}),
            mode=mode,
        )
        for symbol in removed_symbols
    ]
    if closing_note:
        lines.append(closing_note)
    return lines


def missing_position_cleanup_note(mode: str, *, context: str) -> str:
    normalized_mode = str(mode or "").strip().lower()
    if context == "startup":
        if normalized_mode == "paper":
            return (
                "Restore the symbols in config.json, or press 'c' at the console to clear "
                "local paper positions for them before starting, then reload."
            )
        return (
            f"Mode={normalized_mode or 'unknown'}. These are local paper positions kept "
            "for visibility only. Restore the symbols in config.json, or switch to paper "
            "mode and press 'c' at the console to clear the local paper positions before "
            "continuing. This gate only reflects local paper state; it does not submit "
            "or cancel Bitkub orders."
        )
    if normalized_mode == "paper":
        return (
            "Reload was rejected. These are simulated paper positions. "
            "Restore the symbols in config.json, or press 'c' to clear local paper "
            "positions for them, then retry."
        )
    return (
        f"Reload was rejected while mode={normalized_mode or 'unknown'}. These are local "
        "paper positions kept for visibility only. Restore the symbols in config.json, "
        "or switch to paper mode and press 'c' to clear the local paper positions, then "
        "retry. This gate only reflects local paper state; it does not submit or cancel "
        "Bitkub orders."
    )


def mode_notice(
    mode: str,
    active_positions: dict,
    *,
    strategy_execution_wired: bool,
    live_auto_exit_enabled: bool,
) -> tuple[str | None, list[str] | None]:
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
        if strategy_execution_wired:
            lines = [
                "Live mode is wired for guarded strategy-driven entries.",
                "Only symbols in config rules can auto-enter; watchlist symbols remain research-only.",
                "Auto live exit can run separately for exchange holdings when enabled.",
                "Use hotkey M only if you intentionally want to submit the configured manual live order preset.",
            ]
            title = "Live mode is running with guarded auto entry"
        elif live_auto_exit_enabled:
            lines = [
                "Live mode foundation is loaded for guardrail and execution testing.",
                "Strategy-driven live entry remains disconnected from the market loop.",
                "Auto live exit can be enabled separately for exchange holdings.",
                "Use hotkey M only if you intentionally want to submit the configured manual live order preset.",
            ]
            title = "Live mode is in guarded foundation-only state"
        else:
            lines = [
                "Live mode foundation is loaded for guardrail and execution testing.",
                "Strategy-driven live entry remains disconnected from the market loop.",
                "Auto live exit can be enabled separately for exchange holdings.",
                "Use hotkey M only if you intentionally want to submit the configured manual live order preset.",
            ]
            title = "Live mode is in guarded foundation-only state"
        if active_positions:
            lines.append(
                "Existing paper positions remain visible, but they are not managed as live orders."
            )
        return title, lines

    if mode == "shadow-live":
        lines = [
            "Shadow-live mode runs the live candidate pipeline and guardrails without sending exchange orders.",
            "Every shadow action should be reviewable in the trade journal before any live expansion.",
            "Only symbols in config rules are evaluated for shadow auto entry; watchlist symbols remain research-only.",
        ]
        if live_auto_exit_enabled:
            lines.append(
                "Shadow auto exit is evaluated from live holdings context, but the bot records intent instead of submitting exchange orders."
            )
        if active_positions:
            lines.append(
                "Existing paper positions remain visible, but they are not managed as shadow-live orders."
            )
        return "Shadow-live mode is recording guarded trade intents", lines

    lines = [
        "Live execution is intentionally disabled in this build.",
        "Market scan and account snapshots can still run, but no orders or paper trades will execute.",
    ]
    if active_positions:
        lines.append(
            "Existing paper positions remain visible, but they are not managed while live-disabled mode is active."
        )
    return "Live mode is disabled in this build", lines


def execution_guardrail_message(
    mode: str,
    *,
    strategy_execution_wired: bool,
    live_auto_exit_enabled: bool,
) -> str | None:
    if mode == "read-only":
        return "Trading engine is locked by read-only mode"
    if mode == "live":
        if strategy_execution_wired:
            return "Live mode selected; guarded strategy-driven live entry is wired in this build"
        if live_auto_exit_enabled:
            return "Live mode selected; auto live exit is wired while strategy-driven live entry remains disconnected"
        return "Live mode selected; execution guardrails are active and strategy-driven live orders remain disconnected"
    if mode == "shadow-live":
        if strategy_execution_wired:
            return "Shadow-live mode selected; guarded strategy-driven live intents are recorded without sending exchange orders"
        if live_auto_exit_enabled:
            return "Shadow-live mode selected; auto live exit intents are recorded while exchange submission stays disabled"
        return "Shadow-live mode selected; execution guardrails are active and trade intents are recorded only"
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
        if msvcrt is not None and msvcrt.kbhit():
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
    if msvcrt is None:
        print("Interactive key input is unavailable on this platform.")
        return
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
    if msvcrt is None:
        print("Interactive confirmation is unavailable on this platform.")
        return False
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
    version_snapshot = get_app_version_snapshot()
    app_version_label = format_app_version_label(version_snapshot)
    app_version_detail = format_app_version_detail(version_snapshot)
    version_message = f"Application version {app_version_label}"
    print(version_message)
    if app_version_detail:
        print(f"Version details: {app_version_detail}")
    insert_runtime_event(
        created_at=now_text(),
        event_type="app_version",
        severity="info",
        message=version_message,
        details=version_snapshot,
    )

    manual_pause, restore_messages, runtime_state_metadata = load_runtime_state(
        last_zones, positions, daily_stats, cooldowns
    )
    runtime_run_id = new_correlation_id("engine_run")
    shutdown_reason = "operator_quit"
    safety_pause = False
    safety_pause_lines: list[str] | None = None
    latest_prices: dict[str, float] = {}
    account_snapshot: dict | None = None
    report_filter_symbol: str | None = None
    last_retention_cleanup_day: str | None = None
    notice: str | None = None
    notice_lines: list[str] | None = restore_messages or None
    pending_telegram_confirms: dict[str, dict] = {}
    private_api_status = "not configured"
    private_api_capabilities: list[str] | None = None
    selected_execution_order_id: int | None = None
    last_state_reconciliation_at = 0.0
    state_reconciliation_interval_seconds = 300
    telegram_confirm_ttl_seconds = 120
    telegram_poll_error_cooldown_seconds = 180
    unsupported_live_entry_symbols: dict[str, str] = {}
    last_telegram_poll_error_signature: tuple[str, ...] | None = None
    last_telegram_poll_error_logged_at = 0.0
    market_symbol_directory: dict[str, Any] = {
        "symbols": [],
        "rows": [],
        "source_by_symbol": {},
        "exchange_symbols": [],
        "non_exchange_symbols": [],
        "non_exchange_rows": [],
        "error": None,
    }

    def prune_unsupported_live_entry_symbols():
        active_symbols = {str(symbol) for symbol in config.get("rules", {})}
        for symbol in list(unsupported_live_entry_symbols):
            if symbol not in active_symbols:
                unsupported_live_entry_symbols.pop(symbol, None)
                continue

            reason = unsupported_live_entry_symbols.get(symbol)
            source_by_symbol = dict(market_symbol_directory.get("source_by_symbol") or {})
            market_source = str(source_by_symbol.get(symbol) or "").strip().lower()
            if (
                _is_market_source_block_reason(reason)
                and market_source == "exchange"
            ):
                unsupported_live_entry_symbols.pop(symbol, None)

    def refresh_market_symbol_directory(*, source: str) -> None:
        nonlocal market_symbol_directory
        try:
            market_symbol_directory = fetch_market_symbol_directory()
        except Exception as e:
            insert_runtime_event(
                created_at=now_text(),
                event_type="market_symbol_directory",
                severity="warning",
                message="Market symbol directory refresh failed",
                details={"source": source, "error": str(e)},
            )
            return

        non_exchange_rows = list(market_symbol_directory.get("non_exchange_rows") or [])
        if non_exchange_rows:
            insert_runtime_event(
                created_at=now_text(),
                event_type="market_symbol_directory",
                severity="info",
                message="Market symbol directory refreshed",
                details={
                    "source": source,
                    "symbols": len(market_symbol_directory.get("symbols", [])),
                    "non_exchange_symbols": [
                        str(row.get("symbol") or "")
                        for row in non_exchange_rows[:50]
                        if str(row.get("symbol") or "")
                    ],
                },
            )

    def track_non_exchange_live_entry_symbols_from_market_directory():
        prune_unsupported_live_entry_symbols()
        source_by_symbol = dict(market_symbol_directory.get("source_by_symbol") or {})
        blocked_sources = build_non_exchange_symbol_source_map(
            config.get("rules", {}).keys(),
            source_by_symbol=source_by_symbol,
        )
        for symbol, market_source in blocked_sources.items():
            unsupported_live_entry_symbols[symbol] = _unsupported_live_entry_reason(
                source="market_source",
                error_message=market_source,
            )

    def track_unsupported_live_entry_symbols_from_snapshot(snapshot: dict | None):
        prune_unsupported_live_entry_symbols()
        for symbol in unsupported_open_orders_symbol_map(snapshot).keys():
            normalized_symbol = str(symbol).strip().upper()
            if not normalized_symbol:
                continue
            unsupported_live_entry_symbols.setdefault(
                normalized_symbol,
                _unsupported_live_entry_reason(source="open_orders_probe"),
            )

    def remember_unsupported_live_entry_symbol(*, symbol: str, error_message: str) -> bool:
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            return False

        prune_unsupported_live_entry_symbols()
        was_new = normalized_symbol not in unsupported_live_entry_symbols
        unsupported_live_entry_symbols[normalized_symbol] = _unsupported_live_entry_reason(
            source="order_submit",
            error_message=error_message,
        )
        return was_new

    refresh_market_symbol_directory(source="startup")
    track_non_exchange_live_entry_symbols_from_market_directory()

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
            track_unsupported_live_entry_symbols_from_snapshot(account_snapshot)
        else:
            private_api_status = "not configured"
            private_api_capabilities = ["wallet=OFF", "balances=OFF", "open_orders=OFF"]
    except BitkubMissingCredentialsError:
        private_api_status = "missing credentials"
        private_api_capabilities = ["wallet=OFF", "balances=OFF", "open_orders=OFF"]

    startup_mode = str(config.get("mode", "paper"))
    strategy_execution_wired = bool(config.get("live_auto_entry_enabled", False))
    startup_guardrail_message = execution_guardrail_message(
        startup_mode,
        strategy_execution_wired=strategy_execution_wired,
        live_auto_exit_enabled=bool(config.get("live_auto_exit_enabled", False)),
    )
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
        safety_pause_lines = build_missing_position_block_lines(
            prefix="open position exists for removed symbol:",
            removed_symbols=startup_missing_symbols,
            active_positions=positions,
            mode=startup_mode,
            closing_note=missing_position_cleanup_note(startup_mode, context="startup"),
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
        audit_event(
            action_type="safety_pause",
            actor_type="system",
            source="startup",
            target_type="runtime_state",
            target_id="safety_pause",
            old_value={"safety_pause": False},
            new_value={"safety_pause": True},
            status="succeeded",
            message=notice,
            correlation_id=runtime_run_id,
            metadata={"lines": safety_pause_lines},
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
        audit_event(
            action_type="reconciliation_review",
            actor_type="system",
            source="startup",
            target_type="positions",
            target_id="runtime_state",
            status="failed",
            message=notice,
            correlation_id=runtime_run_id,
            metadata={
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
            audit_event(
                action_type="execution_reconciliation",
                actor_type="system",
                source="startup",
                target_type="execution_orders",
                target_id="open",
                status="failed",
                message="Open execution orders require reconciliation review",
                correlation_id=runtime_run_id,
                metadata={"warnings": startup_execution_warnings},
            )

    audit_event(
        action_type="runtime_startup",
        actor_type="system",
        source="startup",
        target_type="engine",
        target_id="main",
        status="succeeded",
        message="Bitkub engine startup completed",
        correlation_id=runtime_run_id,
        metadata={
            "mode": startup_mode,
            "manual_pause": manual_pause,
            "safety_pause": safety_pause,
            "private_api_status": private_api_status,
            "runtime_state": runtime_state_metadata,
            "version": version_snapshot,
        },
    )

    def persist_state():
        nonlocal runtime_state_metadata
        save_runtime_state(
            last_zones,
            positions,
            daily_stats,
            cooldowns,
            manual_pause=manual_pause,
        )
        runtime_state_metadata = {
            "source_path": str(STATE_FILE_PATH),
            "loaded_from_pending": False,
            "saved_at": now_text(),
            "open_positions": len(positions),
            "cooldowns": len(cooldowns),
            "tracked_days": len(daily_stats),
        }

    def notify_telegram(event_type: str, title: str, lines: list[str] | None = None, *, payload: dict | None = None):
        queue_telegram_notification(
            config=config,
            created_at=now_text(),
            event_type=event_type,
            title=title,
            lines=lines,
            payload=payload,
        )

    def flush_telegram_notifications():
        delivery = flush_telegram_outbox(config=config, max_messages=10)
        if delivery["failed"] > 0:
            insert_runtime_event(
                created_at=now_text(),
                event_type="telegram_delivery",
                severity="warning",
                message="Telegram delivery failed for one or more queued notifications",
                details=delivery,
            )

    def telegram_status_lines() -> list[str]:
        return [
            f"mode={trading_mode}",
            f"state={'SAFETY PAUSE' if safety_pause else 'MANUAL PAUSE' if manual_pause else 'RUNNING'}",
            f"live_execution_enabled={'ON' if bool(config.get('live_execution_enabled', False)) else 'OFF'}",
            f"live_auto_entry_enabled={'ON' if bool(config.get('live_auto_entry_enabled', False)) else 'OFF'}",
            f"live_auto_exit_enabled={'ON' if bool(config.get('live_auto_exit_enabled', False)) else 'OFF'}",
            f"watchlist={len(config.get('watchlist_symbols', []))}",
            f"live_rules={len(config['rules'])}",
            f"open_positions={len(positions)} cooldowns={len(active_cooldown_rows())}",
            f"private_api={private_api_status}",
        ]

    def telegram_positions_lines() -> list[str]:
        if not positions:
            return ["No local paper positions."]

        lines: list[str] = []
        for symbol in sorted(positions)[:8]:
            lines.append(
                build_telegram_position_line(
                    symbol=symbol,
                    latest_prices=latest_prices,
                    rules=rules,
                    fee_rate=fee_rate,
                    positions=positions,
                )
            )
        if len(positions) > 8:
            lines.append(f"... and {len(positions) - 8} more position(s)")
        return lines

    def telegram_health_lines() -> list[str]:
        total_trades, total_wins, total_losses, total_pnl = daily_totals()
        guardrails = build_live_execution_guardrails(
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
        lines = [
            f"ready={'YES' if guardrails['ready'] else 'NO'}",
            f"kill_switch={'ON' if guardrails['live_execution_enabled'] else 'OFF'}",
            f"auto_entry={'ON' if guardrails.get('live_auto_entry_enabled') else 'OFF'}",
            f"auto_exit={'ON' if guardrails.get('live_auto_exit_enabled') else 'OFF'}",
            f"open_orders_capability={guardrails.get('open_orders_capability')}",
            f"thb_available={float(guardrails.get('thb_available_balance', 0.0)):,.2f}",
            f"realized_today={total_pnl:,.2f} THB",
        ]
        blocked_reasons = list(guardrails.get("blocked_reasons", []))
        if blocked_reasons:
            lines.append("blocked_reasons:")
            lines.extend(f"- {reason}" for reason in blocked_reasons[:5])
        return lines

    def telegram_live_lines() -> list[str]:
        total_trades, total_wins, total_losses, total_pnl = daily_totals()
        guardrails = build_live_execution_guardrails(
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
        return [
            f"mode={trading_mode}",
            f"ready={'YES' if guardrails['ready'] else 'NO'}",
            f"kill_switch={'ON' if guardrails['live_execution_enabled'] else 'OFF'}",
            f"auto_entry={'ON' if guardrails.get('live_auto_entry_enabled') else 'OFF'}",
            f"auto_exit={'ON' if guardrails.get('live_auto_exit_enabled') else 'OFF'}",
            f"strategy_wired={'YES' if strategy_execution_wired else 'NO'}",
            f"thb_available={float(guardrails.get('thb_available_balance', 0.0)):,.2f}",
            f"blocked_reasons={len(guardrails.get('blocked_reasons', []))}",
            *[
                f"- {reason}"
                for reason in list(guardrails.get("blocked_reasons", []))[:3]
            ],
        ]

    def telegram_config_lines() -> list[str]:
        return [
            f"mode={config.get('mode')}",
            f"interval_seconds={config.get('interval_seconds')}",
            f"fee_rate={config.get('fee_rate')}",
            f"live_execution_enabled={'ON' if bool(config.get('live_execution_enabled', False)) else 'OFF'}",
            f"live_auto_entry_enabled={'ON' if bool(config.get('live_auto_entry_enabled', False)) else 'OFF'}",
            f"live_auto_exit_enabled={'ON' if bool(config.get('live_auto_exit_enabled', False)) else 'OFF'}",
            f"entry_rank_min_score={float(config.get('live_auto_entry_min_score', 0.0)):,.1f}",
            "entry_allowed_biases="
            + ", ".join(
                str(value)
                for value in config.get("live_auto_entry_allowed_biases", [])
            ),
            f"rules={len(config.get('rules', {}))}",
            f"watchlist_symbols={len(config.get('watchlist_symbols', []))}",
            f"telegram_enabled={'ON' if bool(config.get('telegram_enabled', False)) else 'OFF'}",
            f"telegram_control_enabled={'ON' if bool(config.get('telegram_control_enabled', False)) else 'OFF'}",
        ]

    def telegram_holdings_lines() -> list[str]:
        if private_client is None:
            return ["Private API credentials are not configured."]

        snapshot = fetch_account_snapshot(private_client)
        holdings_rows = build_live_holdings_snapshot(
            account_snapshot=snapshot,
            latest_prices=latest_prices,
            latest_filled_execution_orders=fetch_latest_filled_execution_orders_by_symbol(),
        )
        if not holdings_rows:
            return ["No live holdings found."]

        lines: list[str] = []
        for row in holdings_rows[:8]:
            symbol = str(row.get("symbol"))
            available_qty = float(row.get("available_qty", 0.0) or 0.0)
            reserved_qty = float(row.get("reserved_qty", 0.0) or 0.0)
            market_value = float(row.get("market_value_thb", 0.0) or 0.0)
            auto_status = str(row.get("auto_exit_status") or "n/a")
            lines.append(
                f"{symbol}: avail={available_qty:,.8f} reserved={reserved_qty:,.8f} value={market_value:,.2f} THB auto={auto_status}"
            )
        if len(holdings_rows) > 8:
            lines.append(f"... and {len(holdings_rows) - 8} more row(s)")
        return lines

    def telegram_orders_lines() -> list[str]:
        open_orders = fetch_open_execution_orders()
        if not open_orders:
            return ["No open execution orders."]
        lines = []
        for row in open_orders[:8]:
            lines.append(
                f"id={row['id']} {row['symbol']} {row['side']} state={row['state']} updated={row['updated_at']}"
            )
        if len(open_orders) > 8:
            lines.append(f"... and {len(open_orders) - 8} more order(s)")
        return lines

    def telegram_latest_lines() -> list[str]:
        summary = fetch_dashboard_summary(today=today_key())
        latest_execution = summary.get("latest_execution_order")
        latest_reconciliation = summary.get("latest_reconciliation")
        latest_account_snapshot = summary.get("latest_account_snapshot")
        recent_commands = fetch_recent_telegram_command_log(limit=20)
        control_commands = {
            "/buy",
            "/sell",
            "/set_config",
            "/set_rule",
            "/promote_symbol",
            "/pause",
            "/resume",
            "/cancel",
            "/reload",
            "/confirm",
        }
        latest_control_command = next(
            (
                row
                for row in recent_commands
                if str(row.get("command_text") or "").split()[0].split("@")[0].lower()
                in control_commands
            ),
            None,
        )
        recent_runtime_events = fetch_runtime_event_log(limit=20)
        latest_runtime_event = next(
            (
                row
                for row in recent_runtime_events
                if str(row.get("event_type") or "") != "telegram_delivery"
            ),
            recent_runtime_events[0] if recent_runtime_events else None,
        )
        latest_auto_entry_review = next(
            (
                row
                for row in recent_runtime_events
                if str(row.get("event_type") or "") == "auto_live_entry_review"
            ),
            None,
        )
        open_orders_count = len(fetch_open_execution_orders())

        lines: list[str] = [
            "state: "
            f"{'SAFETY PAUSE' if safety_pause else 'MANUAL PAUSE' if manual_pause else 'RUNNING'}",
            f"manual_pause={'ON' if manual_pause else 'OFF'} safety_pause={'ON' if safety_pause else 'OFF'}",
            f"open_execution_orders={open_orders_count}",
        ]
        if latest_execution:
            lines.append(
                "execution: "
                f"id={latest_execution['id']} {latest_execution['symbol']} {latest_execution['side']} {latest_execution['state']}"
            )
            lines.append(f"execution_updated={latest_execution['updated_at']}")
        else:
            lines.append("execution: none")

        if latest_reconciliation:
            lines.append(
                "reconciliation: "
                f"{latest_reconciliation['status']} phase={latest_reconciliation['phase']} positions={latest_reconciliation['positions_count']}"
            )
        if latest_account_snapshot:
            lines.append(
                "account_snapshot: "
                f"{latest_account_snapshot['private_api_status']} @ {latest_account_snapshot['created_at']}"
            )
        if latest_control_command:
            lines.append(
                "latest_control_command: "
                f"{latest_control_command.get('command_text')} -> {latest_control_command.get('status')}"
            )
        else:
            lines.append("latest_control_command: none")
        if latest_runtime_event:
            lines.append(
                "latest_runtime_event: "
                f"{latest_runtime_event.get('event_type')} | {latest_runtime_event.get('severity')} | {latest_runtime_event.get('message')}"
            )
        if latest_auto_entry_review:
            details = latest_auto_entry_review.get("details") or {}
            candidate_count = len(details.get("candidates") or [])
            rejected_count = len(details.get("rejected") or [])
            lines.append(
                "latest_auto_entry_review: "
                f"candidates={candidate_count} rejected={rejected_count} @ {latest_auto_entry_review.get('created_at')}"
            )
            top_candidate = (details.get("candidates") or [None])[0]
            if isinstance(top_candidate, dict):
                lines.append(
                    "top_candidate: "
                    f"{top_candidate.get('symbol')} score={top_candidate.get('ranking_score')} bias={top_candidate.get('trend_bias')}"
                )
        if notice:
            lines.append(f"notice: {notice}")
        return lines

    def cleanup_pending_telegram_confirms():
        now_ts = time.time()
        expire_stale_telegram_command_logs(
            created_before=(
                now_dt() - timedelta(seconds=telegram_confirm_ttl_seconds)
            ).strftime("%Y-%m-%d %H:%M:%S")
        )
        expired_chat_ids = [
            chat_id
            for chat_id, pending in pending_telegram_confirms.items()
            if float(pending.get("expires_at", 0.0)) <= now_ts
        ]
        for chat_id in expired_chat_ids:
            pending_telegram_confirms.pop(chat_id, None)

    def create_telegram_confirmation(*, chat_id: str, action: str, payload: dict, summary_lines: list[str]) -> list[str]:
        cleanup_pending_telegram_confirms()
        correlation_id = new_correlation_id(f"telegram_{action}")
        code = secrets.token_hex(3).upper()
        pending_telegram_confirms[str(chat_id)] = {
            "code": code,
            "action": action,
            "payload": payload,
            "expires_at": time.time() + telegram_confirm_ttl_seconds,
            "correlation_id": correlation_id,
        }
        return [
            f"Confirmation required for {action}.",
            *summary_lines,
            "Confirm command:",
            f"/confirm {code}",
            f"Copy the line above and send it within {telegram_confirm_ttl_seconds} seconds.",
        ]

    def telegram_rule_seed(symbol: str) -> dict:
        existing = config["rules"].get(symbol)
        if existing:
            return dict(existing)

        latest_price = float(latest_prices.get(symbol, 0.0) or 0.0)
        if latest_price > 0:
            buy_below = latest_price * 0.995
            sell_above = latest_price * 1.02
        else:
            buy_below = 1.0
            sell_above = 1.1

        return {
            "buy_below": float(buy_below),
            "sell_above": float(max(sell_above, buy_below * 1.01)),
            "budget_thb": 100.0,
            "stop_loss_percent": 1.2,
            "take_profit_percent": 2.0,
            "max_trades_per_day": 1,
        }

    def apply_saved_config_update(
        *,
        updated_config: dict,
        event_type: str,
        success_title: str,
        source: str = "telegram_confirmation",
        actor_type: str = "telegram",
        actor_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[bool, str, list[str]]:
        nonlocal config, notice, notice_lines, report_filter_symbol
        old_config = config
        saved_config, errors = save_config(updated_config)
        if errors or saved_config is None:
            failed_lines = [str(error) for error in errors] if errors else ["Config save failed."]
            audit_event(
                action_type="config_update",
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
                target_type="config",
                target_id="active",
                new_value=updated_config,
                status="failed",
                message="Config update failed",
                reason="; ".join(failed_lines),
                correlation_id=correlation_id,
                metadata={"event_type": event_type},
            )
            return False, "Config update failed", failed_lines

        config = saved_config
        prune_unsupported_live_entry_symbols()
        filter_was_reset = False
        if report_filter_symbol and report_filter_symbol not in saved_config["rules"]:
            report_filter_symbol = None
            filter_was_reset = True

        change_lines = summarize_config_changes(old_config, saved_config)
        if filter_was_reset:
            change_lines = change_lines + ["report filter reset to ALL"]

        notice = success_title
        notice_lines = change_lines
        insert_runtime_event(
            created_at=now_text(),
            event_type=event_type,
            severity="info",
            message=success_title,
            details={"changes": change_lines},
        )
        audit_config_change(
            old_config=old_config,
            new_config=saved_config,
            actor_type=actor_type,
            actor_id=actor_id,
            source=source,
            message=success_title,
            correlation_id=correlation_id,
            action_type="config_update",
            metadata={
                "event_type": event_type,
                "change_lines": change_lines,
            },
        )
        audit_runtime_mode_change(
            old_config=old_config,
            new_config=saved_config,
            actor_type=actor_type,
            actor_id=actor_id,
            source=source,
            correlation_id=correlation_id,
            message="Runtime mode controls updated",
        )
        persist_state()
        return True, success_title, change_lines

    def submit_telegram_manual_live_order(
        *,
        symbol: str,
        side: str,
        amount_thb: float,
        amount_coin: float,
        rate: float,
        chat_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[bool, str, list[str]]:
        nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
        order_request = {
            "symbol": symbol,
            "side": side,
            "amount_thb": float(amount_thb),
            "amount_coin": float(amount_coin),
            "rate": float(rate),
            "order_type": "limit",
        }
        correlation_id = correlation_id or new_correlation_id("telegram_manual_order")
        audit_event(
            action_type="manual_order",
            actor_type="telegram",
            actor_id=chat_id,
            source="telegram",
            target_type="order_request",
            target_id=symbol,
            symbol=symbol,
            new_value=order_request,
            status="started",
            message="Telegram manual live order requested",
            correlation_id=correlation_id,
        )
        if private_client is None:
            failed_lines = [
                "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before using Telegram live order controls."
            ]
            audit_event(
                action_type="manual_order",
                actor_type="telegram",
                actor_id=chat_id,
                source="telegram",
                target_type="order_request",
                target_id=symbol,
                symbol=symbol,
                new_value=order_request,
                status="failed",
                message="Telegram manual live order requires private API credentials",
                reason="missing_private_api_credentials",
                correlation_id=correlation_id,
            )
            return False, "Telegram live order requires private API credentials", failed_lines

        try:
            account_snapshot = fetch_account_snapshot(private_client)
        except BitkubPrivateClientError as e:
            failed_lines = [str(e)]
            insert_runtime_event(
                created_at=now_text(),
                event_type="manual_live_order",
                severity="error",
                message="Telegram manual live order failed while refreshing account snapshot",
                details={"errors": failed_lines, "symbol": symbol, "side": side},
            )
            audit_event(
                action_type="manual_order",
                actor_type="telegram",
                actor_id=chat_id,
                source="telegram",
                target_type="order_request",
                target_id=symbol,
                symbol=symbol,
                new_value=order_request,
                status="failed",
                message="Telegram manual live order failed while refreshing account snapshot",
                reason="; ".join(failed_lines),
                correlation_id=correlation_id,
            )
            return False, "Telegram manual live order failed", failed_lines

        snapshot_errors = account_snapshot_errors(account_snapshot)
        private_api_capabilities = summarize_account_capabilities(account_snapshot)
        private_api_status = (
            "wallet/balance ready, some order endpoints unavailable"
            if snapshot_errors
            else "wallet/balance/open-orders ready"
        )
        insert_account_snapshot(
            created_at=now_text(),
            source="telegram_manual_live_order",
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

        temp_config = dict(config)
        temp_config["live_manual_order"] = {
            "enabled": True,
            "symbol": symbol,
            "side": side,
            "order_type": "limit",
            "amount_thb": float(amount_thb),
            "amount_coin": float(amount_coin),
            "rate": float(rate),
        }

        try:
            order_record, order_events = submit_manual_live_order(
                client=private_client,
                config=temp_config,
                rules=config["rules"],
                guardrails=live_guardrails,
                available_balances=extract_available_balances(account_snapshot),
                created_at=now_text(),
                correlation_id=correlation_id,
            )
        except LiveExecutionGuardrailError as e:
            failed_lines = str(e).split("; ")
            insert_runtime_event(
                created_at=now_text(),
                event_type="manual_live_order",
                severity="warning",
                message="Telegram manual live order blocked by guardrails",
                details={"errors": failed_lines, "symbol": symbol, "side": side},
            )
            audit_event(
                action_type="manual_order",
                actor_type="telegram",
                actor_id=chat_id,
                source="telegram",
                target_type="order_request",
                target_id=symbol,
                symbol=symbol,
                new_value=order_request,
                status="failed",
                message="Telegram manual live order blocked by guardrails",
                reason="; ".join(failed_lines),
                correlation_id=correlation_id,
                metadata={"guardrails": live_guardrails},
            )
            return False, "Telegram manual live order blocked by guardrails", failed_lines
        except Exception as e:
            failed_lines = [str(e)]
            insert_runtime_event(
                created_at=now_text(),
                event_type="manual_live_order",
                severity="error",
                message="Telegram manual live order submission failed",
                details={"errors": failed_lines, "symbol": symbol, "side": side},
            )
            audit_event(
                action_type="manual_order",
                actor_type="telegram",
                actor_id=chat_id,
                source="telegram",
                target_type="order_request",
                target_id=symbol,
                symbol=symbol,
                new_value=order_request,
                status="failed",
                message="Telegram manual live order submission failed",
                reason="; ".join(failed_lines),
                correlation_id=correlation_id,
            )
            return False, "Telegram manual live order submission failed", failed_lines

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

        success_lines = [
            f"id={execution_order_id} symbol={order_record['symbol']} side={order_record['side']} state={order_record['state']}"
        ]
        if order_record.get("exchange_order_id"):
            success_lines.append(f"exchange_order_id={order_record['exchange_order_id']}")
        insert_runtime_event(
            created_at=now_text(),
            event_type="manual_live_order",
            severity="warning",
            message="Telegram manual live order submitted",
            details={
                "execution_order_id": execution_order_id,
                "symbol": order_record["symbol"],
                "side": order_record["side"],
                "state": order_record["state"],
            },
        )
        audit_event(
            action_type="manual_order",
            actor_type="telegram",
            actor_id=chat_id,
            source="telegram",
            target_type="execution_order",
            target_id=str(execution_order_id),
            symbol=order_record["symbol"],
            new_value={
                "request": order_request,
                "execution_order_id": execution_order_id,
                "state": order_record["state"],
                "exchange_order_id": order_record.get("exchange_order_id"),
            },
            status="succeeded",
            message="Telegram manual live order submitted",
            correlation_id=correlation_id,
            metadata={"guardrails": order_record.get("guardrails")},
        )
        return True, "Telegram manual live order submitted", success_lines

    def execute_telegram_confirmation(*, chat_id: str, code: str) -> tuple[str, list[str], str]:
        cleanup_pending_telegram_confirms()
        pending = pending_telegram_confirms.get(str(chat_id))
        if not pending:
            return (
                "Telegram confirmation not found",
                ["There is no pending command for this chat."],
                "rejected",
            )
        if str(pending.get("code")) != str(code).strip().upper():
            return (
                "Telegram confirmation rejected",
                ["Confirmation code is invalid or expired."],
                "rejected",
            )

        pending_telegram_confirms.pop(str(chat_id), None)
        action = str(pending.get("action") or "")
        payload = dict(pending.get("payload") or {})
        correlation_id = str(pending.get("correlation_id") or "").strip() or None

        if action == "reload":
            reload_config_action(
                source="telegram_confirmation",
                actor_id=chat_id,
                correlation_id=correlation_id,
            )
            return notice or "Config reload completed", notice_lines or ["Config reload finished."], "processed"
        if action == "pause":
            set_manual_pause_action(True, source="telegram_confirmation")
            return notice or "Manual pause enabled", notice_lines or ["Trading loop is manually paused."], "processed"
        if action == "resume":
            set_manual_pause_action(False, source="telegram_confirmation")
            return notice or "Manual pause cleared", notice_lines or ["Trading loop resumed."], "processed"
        if action == "cancel":
            execution_order_id = int(payload.get("execution_order_id", 0) or 0)
            open_execution_orders = sync_selected_execution_order()
            target_order = next(
                (
                    order
                    for order in open_execution_orders
                    if int(order["id"]) == execution_order_id
                ),
                None,
            )
            if target_order is None:
                return (
                    "Live order cancel rejected",
                    [f"id={execution_order_id} is no longer open."],
                    "rejected",
                )
            success, result_title, result_lines = cancel_specific_live_order(
                target_order,
                source="telegram_cancel_command",
            )
            return result_title, result_lines, "processed" if success else "failed"
        if action == "buy":
            success, result_title, result_lines = submit_telegram_manual_live_order(
                symbol=str(payload.get("symbol") or ""),
                side="buy",
                amount_thb=float(payload.get("amount_thb", 0.0) or 0.0),
                amount_coin=float(payload.get("amount_coin", 0.0) or 0.0),
                rate=float(payload.get("rate", 0.0) or 0.0),
                chat_id=chat_id,
                correlation_id=correlation_id,
            )
            return result_title, result_lines, "processed" if success else "failed"
        if action == "sell":
            success, result_title, result_lines = submit_telegram_manual_live_order(
                symbol=str(payload.get("symbol") or ""),
                side="sell",
                amount_thb=float(payload.get("amount_thb", 0.0) or 0.0),
                amount_coin=float(payload.get("amount_coin", 0.0) or 0.0),
                rate=float(payload.get("rate", 0.0) or 0.0),
                chat_id=chat_id,
                correlation_id=correlation_id,
            )
            return result_title, result_lines, "processed" if success else "failed"
        if action == "set_config":
            updated_config = payload.get("updated_config")
            if not isinstance(updated_config, dict):
                return "Config update failed", ["Pending config payload is invalid."], "failed"
            success, result_title, result_lines = apply_saved_config_update(
                updated_config=updated_config,
                event_type="telegram_set_config",
                success_title="Config updated from Telegram",
                source="telegram_confirmation",
                actor_type="telegram",
                actor_id=chat_id,
                correlation_id=correlation_id,
            )
            return result_title, result_lines, "processed" if success else "failed"
        if action == "promote_symbol":
            symbol = str(payload.get("symbol") or "").strip().upper()
            if not symbol:
                return "Promote symbol failed", ["Pending symbol payload is invalid."], "failed"
            updated = dict(config)
            updated_rules = dict(config["rules"])
            if symbol not in updated_rules:
                updated_rules[symbol] = telegram_rule_seed(symbol)
            updated["rules"] = updated_rules
            updated["watchlist_symbols"] = ordered_unique_symbols(
                config.get("watchlist_symbols", []),
                updated_rules.keys(),
                [symbol],
            )
            success, result_title, result_lines = apply_saved_config_update(
                updated_config=updated,
                event_type="telegram_promote_symbol",
                success_title=f"Promoted {symbol} into live rules",
                source="telegram_confirmation",
                actor_type="telegram",
                actor_id=chat_id,
                correlation_id=correlation_id,
            )
            return result_title, result_lines, "processed" if success else "failed"
        if action == "set_rule":
            symbol = str(payload.get("symbol") or "").strip().upper()
            rule = payload.get("rule")
            if not symbol or not isinstance(rule, dict):
                return "Set rule failed", ["Pending rule payload is invalid."], "failed"
            updated = dict(config)
            updated_rules = dict(config["rules"])
            updated_rules[symbol] = {
                "buy_below": float(rule["buy_below"]),
                "sell_above": float(rule["sell_above"]),
                "budget_thb": float(rule["budget_thb"]),
                "stop_loss_percent": float(rule["stop_loss_percent"]),
                "take_profit_percent": float(rule["take_profit_percent"]),
                "max_trades_per_day": int(rule["max_trades_per_day"]),
            }
            updated["rules"] = updated_rules
            updated["watchlist_symbols"] = ordered_unique_symbols(
                config.get("watchlist_symbols", []),
                updated_rules.keys(),
                [symbol],
            )
            success, result_title, result_lines = apply_saved_config_update(
                updated_config=updated,
                event_type="telegram_set_rule",
                success_title=f"Saved rule for {symbol} from Telegram",
                source="telegram_confirmation",
                actor_type="telegram",
                actor_id=chat_id,
                correlation_id=correlation_id,
            )
            return result_title, result_lines, "processed" if success else "failed"

        return (
            "Telegram confirmation failed",
            [f"Unsupported pending action: {action}"],
            "failed",
        )

    def parse_telegram_scalar_value(field_name: str, raw_value: str):
        web_only_fields = {
            "telegram_enabled",
            "telegram_control_enabled",
        }
        boolean_fields = {
            "live_execution_enabled",
            "live_auto_entry_enabled",
            "live_auto_exit_enabled",
            "live_auto_entry_require_ranking",
        }
        integer_fields = {
            "interval_seconds",
            "cooldown_seconds",
            "live_auto_entry_rank_lookback_days",
        }
        float_fields = {
            "fee_rate",
            "live_auto_entry_min_score",
            "live_max_order_thb",
            "live_min_thb_balance",
            "live_slippage_tolerance_percent",
            "live_daily_loss_limit_thb",
        }
        string_fields = {
            "mode",
            "base_url",
            "live_auto_entry_rank_resolution",
        }

        if field_name in web_only_fields:
            raise ValueError(f"{field_name} is web-only and cannot be changed from Telegram")

        if field_name in boolean_fields:
            normalized = str(raw_value).strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"{field_name} expects true/false")
        if field_name in integer_fields:
            return int(raw_value)
        if field_name in float_fields:
            return float(raw_value)
        if field_name in string_fields:
            value = str(raw_value).strip()
            if not value:
                raise ValueError(f"{field_name} cannot be empty")
            return value
        raise ValueError(
            "unsupported field. Allowed fields: "
            "mode, base_url, fee_rate, interval_seconds, cooldown_seconds, "
            "live_execution_enabled, live_auto_entry_enabled, live_auto_exit_enabled, "
            "live_auto_entry_require_ranking, live_auto_entry_rank_resolution, "
            "live_auto_entry_rank_lookback_days, live_auto_entry_min_score, "
            "live_max_order_thb, live_min_thb_balance, "
            "live_slippage_tolerance_percent, live_daily_loss_limit_thb"
        )

    def telegram_set_config_fields_lines() -> list[str]:
        return [
            "Supported /set_config fields:",
            "mode",
            "base_url",
            "fee_rate",
            "interval_seconds",
            "cooldown_seconds",
            "live_execution_enabled",
            "live_auto_entry_enabled",
            "live_auto_exit_enabled",
            "live_auto_entry_require_ranking",
            "live_auto_entry_rank_resolution",
            "live_auto_entry_rank_lookback_days",
            "live_auto_entry_min_score",
            "live_max_order_thb",
            "live_min_thb_balance",
            "live_slippage_tolerance_percent",
            "live_daily_loss_limit_thb",
            "Example:",
            "/set_config live_auto_entry_min_score 55",
        ]

    def process_telegram_commands():
        nonlocal last_telegram_poll_error_logged_at, last_telegram_poll_error_signature
        cleanup_pending_telegram_confirms()
        updates_result = fetch_telegram_command_updates(config=config, limit=10)
        if updates_result.get("errors"):
            error_signature = tuple(
                [str(updates_result.get("error_type") or "unknown")]
                + [str(item) for item in updates_result.get("errors", [])]
            )
            now_ts = time.time()
            should_log = (
                error_signature != last_telegram_poll_error_signature
                or now_ts - last_telegram_poll_error_logged_at >= telegram_poll_error_cooldown_seconds
            )
            if should_log:
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="telegram_delivery",
                    severity=(
                        "info"
                        if str(updates_result.get("error_type") or "") == "config"
                        else "warning"
                    ),
                    message="Telegram command polling failed",
                    details=updates_result,
                )
                last_telegram_poll_error_signature = error_signature
                last_telegram_poll_error_logged_at = now_ts
            return
        last_telegram_poll_error_signature = None
        last_telegram_poll_error_logged_at = 0.0

        for update in updates_result.get("updates", []):
            command_text = str(update.get("command_text") or "").strip()
            if not command_text:
                continue

            command_log_id = insert_telegram_command_log(
                created_at=now_text(),
                update_id=int(update["update_id"]),
                chat_id=str(update["chat_id"]),
                username=str(update.get("username") or ""),
                command_text=command_text,
                status="received",
            )
            if command_log_id is None:
                continue

            authorized = telegram_chat_is_authorized(
                config=config,
                chat_id=str(update["chat_id"]),
            )
            command_parts = command_text.split()
            command_name = command_parts[0].split("@")[0].lower()
            response_lines: list[str]
            response_title: str
            response_status = "processed"

            try:
                if command_name in {"/start", "/help"}:
                    help_topic = (
                        str(command_parts[1]).strip().lower()
                        if len(command_parts) > 1
                        else ""
                    )
                    if help_topic == "config":
                        response_title = "Bitkub Telegram Config Help"
                        response_lines = telegram_set_config_fields_lines()
                    else:
                        response_title = "Bitkub Telegram Commands"
                        response_lines = [
                            "Read only: /status /health /positions /holdings /orders /latest /live /config",
                            "Control: /buy /sell /set_config /set_rule /promote_symbol /pause /resume /cancel /reload",
                            "Confirm: /confirm <code>",
                            "Config help: /help config or /set_config_fields",
                            "Quick examples:",
                            "/buy THB_BCH 100 15400",
                            "/set_config live_auto_entry_min_score 55",
                            "/set_rule THB_BCH 15000 15700 100 1.5 2.0 1",
                        ]
                elif command_name == "/set_config_fields":
                    response_title = "Bitkub Telegram Config Help"
                    response_lines = telegram_set_config_fields_lines()
                elif command_name == "/status":
                    response_title = "Bitkub Bot Status"
                    response_lines = telegram_status_lines()
                elif command_name == "/health":
                    response_title = "Bitkub Bot Health"
                    response_lines = telegram_health_lines()
                elif command_name == "/positions":
                    response_title = "Bitkub Local Positions"
                    response_lines = telegram_positions_lines()
                elif command_name == "/holdings":
                    response_title = "Bitkub Live Holdings"
                    response_lines = telegram_holdings_lines()
                elif command_name == "/orders":
                    response_title = "Bitkub Open Orders"
                    response_lines = telegram_orders_lines()
                elif command_name == "/latest":
                    response_title = "Bitkub Latest Activity"
                    response_lines = telegram_latest_lines()
                elif command_name == "/live":
                    response_title = "Bitkub Live Execution"
                    response_lines = telegram_live_lines()
                elif command_name == "/config":
                    response_title = "Bitkub Config Summary"
                    response_lines = telegram_config_lines()
                elif command_name == "/buy":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /buy."]
                        response_status = "rejected"
                    elif len(command_parts) < 4:
                        response_title = "Telegram buy usage"
                        response_lines = [
                            "Use: /buy SYMBOL AMOUNT_THB RATE",
                            "Example:",
                            "/buy THB_BCH 100 15400",
                        ]
                        response_status = "rejected"
                    else:
                        symbol = str(command_parts[1]).strip().upper()
                        try:
                            amount_thb = float(command_parts[2])
                            rate = float(command_parts[3])
                        except ValueError:
                            response_title = "Telegram buy usage"
                            response_lines = ["AMOUNT_THB and RATE must be numeric."]
                            response_status = "rejected"
                        else:
                            if symbol not in config["rules"]:
                                response_title = "Telegram buy rejected"
                                response_lines = [f"{symbol} is not present in config rules."]
                                response_status = "rejected"
                            else:
                                response_title = "Telegram confirmation required"
                                response_lines = create_telegram_confirmation(
                                    chat_id=str(update["chat_id"]),
                                    action="buy",
                                    payload={
                                        "symbol": symbol,
                                        "amount_thb": amount_thb,
                                        "amount_coin": 0.0,
                                        "rate": rate,
                                    },
                                    summary_lines=[
                                        "Requested action: live BUY",
                                        f"symbol={symbol}",
                                        f"amount_thb={amount_thb:,.2f}",
                                        f"rate={rate:,.8f}",
                                    ],
                                )
                                response_status = "pending_confirmation"
                elif command_name == "/sell":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /sell."]
                        response_status = "rejected"
                    elif len(command_parts) < 4:
                        response_title = "Telegram sell usage"
                        response_lines = [
                            "Use: /sell SYMBOL AMOUNT_COIN RATE",
                            "Example:",
                            "/sell THB_KUB 3.2 30",
                        ]
                        response_status = "rejected"
                    else:
                        symbol = str(command_parts[1]).strip().upper()
                        try:
                            amount_coin = float(command_parts[2])
                            rate = float(command_parts[3])
                        except ValueError:
                            response_title = "Telegram sell usage"
                            response_lines = ["AMOUNT_COIN and RATE must be numeric."]
                            response_status = "rejected"
                        else:
                            if symbol not in config["rules"]:
                                response_title = "Telegram sell rejected"
                                response_lines = [f"{symbol} is not present in config rules."]
                                response_status = "rejected"
                            else:
                                response_title = "Telegram confirmation required"
                                response_lines = create_telegram_confirmation(
                                    chat_id=str(update["chat_id"]),
                                    action="sell",
                                    payload={
                                        "symbol": symbol,
                                        "amount_thb": 0.0,
                                        "amount_coin": amount_coin,
                                        "rate": rate,
                                    },
                                    summary_lines=[
                                        "Requested action: live SELL",
                                        f"symbol={symbol}",
                                        f"amount_coin={amount_coin:,.8f}",
                                        f"rate={rate:,.8f}",
                                    ],
                                )
                                response_status = "pending_confirmation"
                elif command_name == "/set_config":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /set_config."]
                        response_status = "rejected"
                    elif len(command_parts) < 3:
                        response_title = "Telegram set_config usage"
                        response_lines = [
                            "Use /set_config <field> <value>",
                            "Example: /set_config live_auto_entry_min_score 55",
                        ]
                        response_status = "rejected"
                    else:
                        field_name = str(command_parts[1]).strip()
                        raw_value = " ".join(command_parts[2:]).strip()
                        try:
                            parsed_value = parse_telegram_scalar_value(field_name, raw_value)
                        except Exception as e:
                            response_title = "Telegram set_config rejected"
                            response_lines = [str(e)]
                            response_status = "rejected"
                        else:
                            updated = dict(config)
                            updated[field_name] = parsed_value
                            response_title = "Telegram confirmation required"
                            response_lines = create_telegram_confirmation(
                                chat_id=str(update["chat_id"]),
                                action="set_config",
                                payload={"updated_config": updated},
                                summary_lines=[
                                    "Requested action: config update",
                                    f"{field_name}: {config.get(field_name)} -> {parsed_value}",
                                ],
                            )
                            response_status = "pending_confirmation"
                elif command_name == "/set_rule":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /set_rule."]
                        response_status = "rejected"
                    elif len(command_parts) < 8:
                        response_title = "Telegram set_rule usage"
                        response_lines = [
                            "Use: /set_rule SYMBOL BUY_BELOW SELL_ABOVE BUDGET STOP_LOSS TAKE_PROFIT MAX_TRADES",
                            "Example:",
                            "/set_rule THB_BCH 15000 15700 100 1.5 2.0 1",
                        ]
                        response_status = "rejected"
                    else:
                        symbol = str(command_parts[1]).strip().upper()
                        try:
                            rule = {
                                "buy_below": float(command_parts[2]),
                                "sell_above": float(command_parts[3]),
                                "budget_thb": float(command_parts[4]),
                                "stop_loss_percent": float(command_parts[5]),
                                "take_profit_percent": float(command_parts[6]),
                                "max_trades_per_day": int(command_parts[7]),
                            }
                        except ValueError:
                            response_title = "Telegram set_rule rejected"
                            response_lines = ["Rule values must be numeric and max_trades_per_day must be an integer."]
                            response_status = "rejected"
                        else:
                            response_title = "Telegram confirmation required"
                            response_lines = create_telegram_confirmation(
                                chat_id=str(update["chat_id"]),
                                action="set_rule",
                                payload={"symbol": symbol, "rule": rule},
                                summary_lines=[
                                    f"Requested action: save rule for {symbol}",
                                    f"buy_below={rule['buy_below']}",
                                    f"sell_above={rule['sell_above']}",
                                    f"budget_thb={rule['budget_thb']}",
                                    f"stop_loss_percent={rule['stop_loss_percent']}",
                                    f"take_profit_percent={rule['take_profit_percent']}",
                                    f"max_trades_per_day={rule['max_trades_per_day']}",
                                ],
                            )
                            response_status = "pending_confirmation"
                elif command_name == "/promote_symbol":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /promote_symbol."]
                        response_status = "rejected"
                    elif len(command_parts) < 2:
                        response_title = "Telegram promote_symbol usage"
                        response_lines = ["Use /promote_symbol <symbol>"]
                        response_status = "rejected"
                    else:
                        symbol = str(command_parts[1]).strip().upper()
                        if not symbol:
                            response_title = "Telegram promote_symbol rejected"
                            response_lines = ["Symbol cannot be empty."]
                            response_status = "rejected"
                        elif symbol in config["rules"]:
                            response_title = "Telegram promote_symbol skipped"
                            response_lines = [f"{symbol} is already present in live rules."]
                            response_status = "rejected"
                        else:
                            response_title = "Telegram confirmation required"
                            response_lines = create_telegram_confirmation(
                                chat_id=str(update["chat_id"]),
                                action="promote_symbol",
                                payload={"symbol": symbol},
                                summary_lines=[
                                    "Requested action: promote symbol into live rules",
                                    f"symbol={symbol}",
                                ],
                            )
                            response_status = "pending_confirmation"
                elif command_name == "/pause":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /pause."]
                        response_status = "rejected"
                    else:
                        response_title = "Telegram confirmation required"
                        response_lines = create_telegram_confirmation(
                            chat_id=str(update["chat_id"]),
                            action="pause",
                            payload={},
                            summary_lines=["Requested action: manual pause"],
                        )
                        response_status = "pending_confirmation"
                elif command_name == "/resume":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /resume."]
                        response_status = "rejected"
                    else:
                        response_title = "Telegram confirmation required"
                        response_lines = create_telegram_confirmation(
                            chat_id=str(update["chat_id"]),
                            action="resume",
                            payload={},
                            summary_lines=["Requested action: manual resume"],
                        )
                        response_status = "pending_confirmation"
                elif command_name == "/cancel":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /cancel."]
                        response_status = "rejected"
                    elif len(command_parts) < 2:
                        response_title = "Telegram cancel usage"
                        response_lines = [
                            "Use /cancel <execution_order_id>",
                            *telegram_orders_lines()[:5],
                        ]
                        response_status = "rejected"
                    else:
                        try:
                            execution_order_id = int(command_parts[1])
                        except ValueError:
                            response_title = "Telegram cancel usage"
                            response_lines = ["Execution order id must be an integer."]
                            response_status = "rejected"
                        else:
                            target_order = next(
                                (
                                    order
                                    for order in fetch_open_execution_orders()
                                    if int(order["id"]) == execution_order_id
                                ),
                                None,
                            )
                            if target_order is None:
                                response_title = "Live order cancel rejected"
                                response_lines = [f"id={execution_order_id} is not currently open."]
                                response_status = "rejected"
                            else:
                                response_title = "Telegram confirmation required"
                                response_lines = create_telegram_confirmation(
                                    chat_id=str(update["chat_id"]),
                                    action="cancel",
                                    payload={"execution_order_id": execution_order_id},
                                    summary_lines=[
                                        f"id={execution_order_id}",
                                        f"symbol={target_order['symbol']}",
                                        f"side={target_order['side']}",
                                        f"state={target_order['state']}",
                                    ],
                                )
                                response_status = "pending_confirmation"
                elif command_name == "/reload":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /reload."]
                        response_status = "rejected"
                    else:
                        response_title = "Telegram confirmation required"
                        response_lines = create_telegram_confirmation(
                            chat_id=str(update["chat_id"]),
                            action="reload",
                            payload={},
                            summary_lines=["Requested action: config reload"],
                        )
                        response_status = "pending_confirmation"
                elif command_name == "/confirm":
                    if not authorized:
                        response_title = "Telegram command rejected"
                        response_lines = ["This chat is not authorized to run /confirm."]
                        response_status = "rejected"
                    elif len(command_parts) < 2:
                        response_title = "Telegram confirmation usage"
                        response_lines = ["Use /confirm <code>"]
                        response_status = "rejected"
                    else:
                        response_title, response_lines, response_status = execute_telegram_confirmation(
                            chat_id=str(update["chat_id"]),
                            code=command_parts[1],
                        )
                else:
                    response_title = "Unknown Telegram command"
                    response_lines = [
                        "Unknown command.",
                        "Use /help to see all commands.",
                        "Use /help config to list /set_config fields.",
                        "Quick: /status /orders /latest",
                        "Examples:",
                        "/buy THB_BCH 100 15400",
                        "/set_rule THB_BCH 15000 15700 100 1.5 2.0 1",
                    ]
                    response_status = "rejected"
            except Exception as e:
                response_title = "Telegram command failed"
                response_lines = [f"{command_name}: {e}"]
                response_status = "failed"

            response_text = "\n".join([response_title] + response_lines)
            try:
                send_telegram_direct_message(
                    config=config,
                    chat_id=str(update["chat_id"]),
                    text=response_text,
                )
            except Exception as e:
                response_text = f"{response_text}\nTelegram send failed: {e}"
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="telegram_delivery",
                    severity="warning",
                    message="Telegram command response failed to send",
                    details={
                        "chat_id": str(update["chat_id"]),
                        "command_text": command_text,
                        "exception": str(e),
                    },
                )

            update_telegram_command_log(
                command_log_id=command_log_id,
                status=response_status,
                response_text=response_text,
            )

    def run_sqlite_retention_cleanup(*, source: str, force: bool):
        nonlocal last_retention_cleanup_day
        current_day = today_key()
        correlation_id = new_correlation_id("retention_cleanup")

        if not force and last_retention_cleanup_day == current_day:
            return

        try:
            cleanup_summary = cleanup_sqlite_retention(config=config)
        except Exception as e:
            insert_runtime_event(
                created_at=now_text(),
                event_type="sqlite_retention_cleanup",
                severity="error",
                message="SQLite retention cleanup failed",
                details={
                    "source": source,
                    "exception": str(e),
                },
            )
            audit_event(
                action_type="retention_cleanup",
                actor_type=audit_actor_type_from_source(source),
                source=source,
                target_type="sqlite",
                target_id="retention",
                status="failed",
                message="SQLite retention cleanup failed",
                reason=str(e),
                correlation_id=correlation_id,
                metadata={"force": force},
            )
            return

        last_retention_cleanup_day = current_day
        deleted_rows = dict(cleanup_summary.get("deleted_rows", {}))
        deleted_total = int(cleanup_summary.get("deleted_total", 0) or 0)
        archived_total = int(cleanup_summary.get("archived_total", 0) or 0)

        if deleted_total > 0 or archived_total > 0 or force:
            insert_runtime_event(
                created_at=now_text(),
                event_type="sqlite_retention_cleanup",
                severity="info",
                message=(
                    "SQLite retention cleanup completed; "
                    f"archived {archived_total} records and removed {deleted_total} total rows"
                ),
                details={
                    "source": source,
                    "deleted_rows": deleted_rows,
                    "deleted_total": deleted_total,
                    "archived_total": archived_total,
                    "archive": cleanup_summary.get("archive", {}),
                    "archive_delete_results": cleanup_summary.get("archive_delete_results", {}),
                },
            )
            audit_event(
                action_type="retention_cleanup",
                actor_type=audit_actor_type_from_source(source),
                source=source,
                target_type="sqlite",
                target_id="retention",
                status="succeeded",
                message="SQLite retention cleanup completed",
                correlation_id=correlation_id,
                metadata={
                    "force": force,
                    "deleted_rows": deleted_rows,
                    "deleted_total": deleted_total,
                    "archived_total": archived_total,
                    "archive": cleanup_summary.get("archive", {}),
                    "archive_delete_results": cleanup_summary.get("archive_delete_results", {}),
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

    def run_state_reconciliation(*, source: str, force: bool):
        nonlocal account_snapshot
        nonlocal private_api_capabilities
        nonlocal private_api_status
        nonlocal last_state_reconciliation_at

        current_time = time.time()
        if (
            not force
            and current_time - last_state_reconciliation_at
            < float(state_reconciliation_interval_seconds)
        ):
            return None

        correlation_id = new_correlation_id("state_reconciliation")
        started_at = now_text()
        refresh_errors: list[str] = []
        corrected_orders: list[dict[str, Any]] = []
        snapshot_errors: list[str] = []
        exchange_snapshot_created_at: str | None = None
        snapshot_for_reconciliation: dict[str, Any] | None = None
        account_sync_status = "unavailable" if private_client is None else "failed"

        tracked_open_orders = fetch_open_execution_orders()

        if private_client is not None:
            try:
                snapshot_for_reconciliation = fetch_account_snapshot(private_client)
                exchange_snapshot_created_at = now_text()
                account_snapshot = snapshot_for_reconciliation
                track_unsupported_live_entry_symbols_from_snapshot(account_snapshot)
                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                private_api_status = (
                    "wallet/balance ready, some order endpoints unavailable"
                    if snapshot_errors
                    else "wallet/balance/open-orders ready"
                )
                account_sync_status = "partial" if snapshot_errors else "ready"
                insert_account_snapshot(
                    created_at=exchange_snapshot_created_at,
                    source=f"state_reconciliation_{source}",
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )
            except BitkubPrivateClientError as e:
                refresh_errors.append(f"account snapshot refresh failed: {e}")
                private_api_status = "private API error"
                account_sync_status = "failed"

            for open_order in tracked_open_orders:
                execution_order_id = int(open_order["id"])
                try:
                    refreshed_record, refresh_events = refresh_live_order_from_exchange(
                        client=private_client,
                        order_record=open_order,
                        occurred_at=now_text(),
                    )
                except Exception as e:
                    refresh_errors.append(
                        f"id={execution_order_id} symbol={open_order['symbol']} refresh failed: {e}"
                    )
                    continue

                if (
                    refreshed_record["state"] != open_order["state"]
                    or refreshed_record["updated_at"] != open_order["updated_at"]
                    or refreshed_record.get("exchange_order_id")
                    != open_order.get("exchange_order_id")
                    or refreshed_record.get("message") != open_order.get("message")
                ):
                    persist_execution_order_changes(
                        execution_order_id=execution_order_id,
                        order_record=refreshed_record,
                        order_events=refresh_events,
                    )
                    corrected_orders.append(
                        {
                            "execution_order_id": execution_order_id,
                            "symbol": refreshed_record["symbol"],
                            "side": refreshed_record["side"],
                            "from_state": open_order["state"],
                            "to_state": refreshed_record["state"],
                            "exchange_order_id": refreshed_record.get("exchange_order_id"),
                        }
                    )

        current_open_execution_orders = fetch_open_execution_orders()
        latest_filled_orders = fetch_latest_filled_execution_orders_by_symbol()
        live_holdings_rows = build_live_holdings_snapshot(
            account_snapshot=snapshot_for_reconciliation,
            latest_prices=latest_prices,
            latest_filled_execution_orders=latest_filled_orders,
        )
        findings = collect_runtime_reconciliation_findings(
            execution_orders=current_open_execution_orders,
            live_holdings_rows=live_holdings_rows,
            account_snapshot=snapshot_for_reconciliation,
            runtime_state_metadata=runtime_state_metadata,
        )
        mismatch_summary = dict(findings.get("mismatch_counts") or {})
        stale_pending_count = int(mismatch_summary.get("stale_pending", 0) or 0)
        unresolved_count = int(findings.get("unresolved_count", 0) or 0)
        if snapshot_errors:
            unresolved_count += len(snapshot_errors)
        if refresh_errors:
            unresolved_count += len(refresh_errors)

        if account_sync_status == "failed":
            status = "error"
        elif unresolved_count > 0 or account_sync_status == "partial":
            status = "warning"
        elif account_sync_status == "unavailable":
            status = "unavailable"
        else:
            status = "ok"

        warning_messages = list(findings.get("messages") or [])
        if snapshot_errors:
            warning_messages.extend(snapshot_errors)
        if refresh_errors:
            warning_messages.extend(refresh_errors)

        insert_reconciliation_result(
            created_at=started_at,
            phase=f"state_{source}",
            status=status,
            warnings=warning_messages,
            positions_count=len(positions),
            exchange_balances=extract_available_balances(snapshot_for_reconciliation),
        )
        insert_state_reconciliation_run(
            created_at=started_at,
            source=source,
            status=status,
            account_sync_status=account_sync_status,
            runtime_state_status=str(findings.get("runtime_state_status") or "unknown"),
            local_open_orders_count=int(findings.get("local_open_orders_count", 0) or 0),
            exchange_open_orders_count=int(
                findings.get("exchange_open_orders_count", 0) or 0
            ),
            corrected_order_count=len(corrected_orders),
            unresolved_count=unresolved_count,
            stale_pending_count=stale_pending_count,
            mismatch_summary=mismatch_summary,
            mismatch_details=dict(findings.get("mismatches") or {}),
            correction_summary={
                "corrected_orders": corrected_orders[:50],
                "corrected_order_count": len(corrected_orders),
            },
            notes={
                "correlation_id": correlation_id,
                "force": bool(force),
                "exchange_snapshot_created_at": exchange_snapshot_created_at,
                "snapshot_errors": snapshot_errors[:20],
                "refresh_errors": refresh_errors[:50],
                "runtime_state_saved_at": findings.get("runtime_state_saved_at"),
            },
        )

        if force or status != "ok" or corrected_orders:
            insert_runtime_event(
                created_at=started_at,
                event_type="state_reconciliation",
                severity=(
                    "error"
                    if status == "error"
                    else "warning"
                    if status in {"warning", "unavailable"}
                    else "info"
                ),
                message=(
                    "State reconciliation completed"
                    if status == "ok"
                    else "State reconciliation requires review"
                ),
                details={
                    "source": source,
                    "status": status,
                    "account_sync_status": account_sync_status,
                    "runtime_state_status": findings.get("runtime_state_status"),
                    "mismatch_summary": mismatch_summary,
                    "corrections": corrected_orders[:50],
                    "snapshot_errors": snapshot_errors[:20],
                    "refresh_errors": refresh_errors[:50],
                },
            )

        audit_event(
            action_type="state_reconciliation",
            actor_type="system",
            source=source,
            target_type="runtime_state",
            target_id="engine",
            status=status,
            message=(
                "Structured state reconciliation completed"
                if status == "ok"
                else "Structured state reconciliation recorded mismatches"
            ),
            correlation_id=correlation_id,
            metadata={
                "account_sync_status": account_sync_status,
                "runtime_state_status": findings.get("runtime_state_status"),
                "mismatch_summary": mismatch_summary,
                "corrected_order_count": len(corrected_orders),
                "unresolved_count": unresolved_count,
                "exchange_snapshot_created_at": exchange_snapshot_created_at,
            },
        )

        last_state_reconciliation_at = current_time
        sync_selected_execution_order()
        return {
            "status": status,
            "account_sync_status": account_sync_status,
            "unresolved_count": unresolved_count,
            "corrected_order_count": len(corrected_orders),
        }

    sync_selected_execution_order()
    run_state_reconciliation(source="startup", force=True)

    while True:
        try:
            rules = config["rules"]
            trading_mode = str(config.get("mode", "paper"))
            shadow_live_mode = trading_mode == "shadow-live"
            trading_enabled = trading_mode == "paper"
            live_auto_entry_enabled = bool(config.get("live_auto_entry_enabled", False))
            live_auto_exit_enabled = bool(config.get("live_auto_exit_enabled", False))
            strategy_execution_wired = trading_mode in {"live", "shadow-live"} and live_auto_entry_enabled
            interval_seconds = int(config["interval_seconds"])
            fee_rate = float(config["fee_rate"])

            def record_trade_journal(
                *,
                channel: str,
                status: str,
                symbol: str,
                side: str | None = None,
                signal_reason: str | None = None,
                exit_reason: str | None = None,
                request_rate: float | None = None,
                latest_price: float | None = None,
                amount_thb: float | None = None,
                amount_coin: float | None = None,
                details: dict[str, Any] | None = None,
            ) -> int:
                return insert_trade_journal(
                    created_at=now_text(),
                    trading_mode=trading_mode,
                    channel=channel,
                    status=status,
                    symbol=symbol,
                    side=side,
                    signal_reason=signal_reason,
                    exit_reason=exit_reason,
                    request_rate=request_rate,
                    latest_price=latest_price,
                    amount_thb=amount_thb,
                    amount_coin=amount_coin,
                    details=details,
                )

            def activate_safety_pause(
                reason: str,
                lines: list[str],
                *,
                immediate: bool,
                source: str = "runtime",
            ):
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
                    details={"lines": safety_pause_lines, "immediate": immediate, "source": source},
                )
                audit_event(
                    action_type="safety_pause",
                    actor_type=audit_actor_type_from_source(source),
                    source=source,
                    target_type="runtime_state",
                    target_id="safety_pause",
                    old_value={"safety_pause": False},
                    new_value={"safety_pause": True},
                    status="succeeded",
                    message=reason,
                    correlation_id=runtime_run_id,
                    metadata={"lines": safety_pause_lines, "immediate": immediate},
                )
                if should_queue_safety_pause_telegram_notification(source=source):
                    notify_telegram(
                        "safety_pause",
                        reason,
                        safety_pause_lines,
                        payload={"immediate": immediate, "source": source},
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

            def reload_config_action(
                *,
                source: str = "console",
                actor_id: str | None = None,
                correlation_id: str | None = None,
            ):
                nonlocal config, notice, notice_lines, report_filter_symbol
                old_config = config
                new_config, errors = reload_config()
                actor_type = audit_actor_type_from_source(source)
                correlation_id = correlation_id or new_correlation_id("config_reload")

                if errors or new_config is None:
                    activate_safety_pause(
                        "Safety pause: invalid config.json reload was rejected",
                        errors,
                        immediate=True,
                        source=source,
                    )
                    audit_event(
                        action_type="config_reload",
                        actor_type=actor_type,
                        source=source,
                        target_type="config",
                        target_id="active",
                        status="failed",
                        message="Config reload rejected",
                        reason="; ".join(errors),
                        actor_id=actor_id,
                        correlation_id=correlation_id,
                    )
                    return True

                removed_symbols = missing_position_symbols(new_config["rules"], positions)
                if removed_symbols:
                    reload_mode = str(new_config.get("mode", config.get("mode", "paper")))
                    activate_safety_pause(
                        "Safety pause: config reload would leave open positions unmanaged",
                        build_missing_position_block_lines(
                            prefix="open position still active for removed symbol:",
                            removed_symbols=removed_symbols,
                            active_positions=positions,
                            mode=reload_mode,
                            closing_note=missing_position_cleanup_note(
                                reload_mode, context="reload"
                            ),
                        ),
                        immediate=True,
                        source=source,
                    )
                    audit_event(
                        action_type="config_reload",
                        actor_type=actor_type,
                        source=source,
                        target_type="config",
                        target_id="active",
                        status="failed",
                        message="Config reload rejected because open positions would become unmanaged",
                        reason="removed_symbols_with_open_positions",
                        actor_id=actor_id,
                        correlation_id=correlation_id,
                        metadata={"removed_symbols": removed_symbols},
                    )
                    return True

                config = new_config
                prune_unsupported_live_entry_symbols()
                refresh_market_symbol_directory(source="config_reload")
                track_non_exchange_live_entry_symbols_from_market_directory()
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
                audit_config_change(
                    old_config=old_config,
                    new_config=new_config,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    source=source,
                    message=notice,
                    correlation_id=correlation_id,
                    action_type="config_reload",
                    metadata={"change_lines": change_lines},
                )
                audit_runtime_mode_change(
                    old_config=old_config,
                    new_config=new_config,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    source=source,
                    correlation_id=correlation_id,
                    message="Runtime mode controls reloaded",
                )
                if should_queue_config_reload_telegram_notification(source=source):
                    notify_telegram(
                        "config_reload",
                        notice,
                        change_lines[:8],
                        payload={"changes": change_lines, "source": source},
                    )
                new_mode = str(new_config.get("mode", "paper"))
                guardrail_message = execution_guardrail_message(
                    new_mode,
                    strategy_execution_wired=bool(new_config.get("live_auto_entry_enabled", False)),
                    live_auto_exit_enabled=bool(new_config.get("live_auto_exit_enabled", False)),
                )
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
                target_state = not manual_pause
                return set_manual_pause_action(target_state)

            def set_manual_pause_action(target_state: bool, *, source: str = "console"):
                nonlocal manual_pause, notice, notice_lines
                if not target_state and safety_pause:
                    notice = "Manual resume blocked while safety pause is active"
                    notice_lines = safety_pause_lines or [
                        "Fix the safety condition and press R to clear the safety pause."
                    ]
                    audit_event(
                        action_type="manual_pause_change",
                        actor_type=audit_actor_type_from_source(source),
                        source=source,
                        target_type="runtime_state",
                        target_id="manual_pause",
                        old_value={"manual_pause": manual_pause},
                        new_value={"manual_pause": target_state},
                        status="failed",
                        message=notice,
                        reason="safety_pause_active",
                        correlation_id=runtime_run_id,
                    )
                    return True

                if manual_pause == target_state:
                    notice = (
                        "Manual pause already enabled"
                        if manual_pause
                        else "Manual pause already cleared"
                    )
                    notice_lines = None
                    return True

                previous_state = manual_pause
                manual_pause = target_state
                notice = "Manual pause enabled" if manual_pause else "Manual pause cleared"
                notice_lines = None
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="manual_pause",
                    severity="info",
                    message=notice,
                    details={"manual_pause": manual_pause},
                )
                audit_event(
                    action_type="manual_pause_change",
                    actor_type=audit_actor_type_from_source(source),
                    source=source,
                    target_type="runtime_state",
                    target_id="manual_pause",
                    old_value={"manual_pause": previous_state},
                    new_value={"manual_pause": manual_pause},
                    status="succeeded",
                    message=notice,
                    correlation_id=runtime_run_id,
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
                    "retention_status": db_storage.get("retention_status"),
                    "retention_days": {
                        "market_snapshots": int(
                            config["market_snapshot_hot_retention_days"]
                        ),
                        "signal_logs": int(config["signal_log_hot_retention_days"]),
                        "runtime_events": int(config["runtime_event_retention_days"]),
                        "account_snapshots": int(
                            config["account_snapshot_hot_retention_days"]
                        ),
                        "reconciliation_results": int(
                            config["reconciliation_hot_retention_days"]
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
                correlation_id = new_correlation_id("wallet_import")
                if safety_pause:
                    notice = "Wallet import blocked while safety pause is active"
                    notice_lines = safety_pause_lines or [
                        "Fix the safety condition and press R before importing balances into paper positions."
                    ]
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="failed",
                        message=notice,
                        reason="safety_pause_active",
                        correlation_id=correlation_id,
                    )
                    return True

                if trading_mode != "paper":
                    notice = "Wallet import is available only in paper mode"
                    notice_lines = [
                        "Switch mode to paper in config.json, press R, then import wallet balances again.",
                        "Read-only and live-disabled modes do not manage imported paper positions.",
                    ]
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="failed",
                        message=notice,
                        reason="trading_mode_not_paper",
                        correlation_id=correlation_id,
                        metadata={"mode": trading_mode},
                    )
                    return True

                if private_client is None:
                    notice = "Wallet import requires private API credentials"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before importing wallet balances."
                    ]
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="failed",
                        message=notice,
                        reason="missing_private_api_credentials",
                        correlation_id=correlation_id,
                    )
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
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="failed",
                        message=notice,
                        reason="; ".join(notice_lines),
                        correlation_id=correlation_id,
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
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="failed",
                        message=notice,
                        reason="no_readable_balances",
                        correlation_id=correlation_id,
                        metadata={"private_api_status": private_api_status},
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
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="failed",
                        message=notice,
                        reason="; ".join(notice_lines),
                        correlation_id=correlation_id,
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
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="succeeded",
                        message=notice,
                        correlation_id=correlation_id,
                        metadata={"imported": imported_lines, "skipped": skipped_lines},
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
                    audit_event(
                        action_type="wallet_import",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="paper_positions",
                        target_id="runtime_state",
                        status="failed",
                        message=notice,
                        reason="nothing_imported",
                        correlation_id=correlation_id,
                        metadata={"skipped": skipped_lines},
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
                audit_event(
                    action_type="clear_paper_positions",
                    actor_type="manual",
                    source="console_hotkey",
                    target_type="paper_positions",
                    target_id="runtime_state",
                    old_value={"symbols": removed_symbols},
                    new_value={"symbols": []},
                    status="succeeded",
                    message=notice,
                    correlation_id=runtime_run_id,
                )
                persist_state()
                return True

            def submit_manual_live_order_action():
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                correlation_id = new_correlation_id("console_manual_order")
                manual_order = dict(config.get("live_manual_order", {}))
                order_request = {
                    "symbol": str(manual_order.get("symbol") or ""),
                    "side": str(manual_order.get("side") or ""),
                    "order_type": str(manual_order.get("order_type") or ""),
                    "amount_thb": float(manual_order.get("amount_thb", 0.0) or 0.0),
                    "amount_coin": float(manual_order.get("amount_coin", 0.0) or 0.0),
                    "rate": float(manual_order.get("rate", 0.0) or 0.0),
                }
                audit_event(
                    action_type="manual_order",
                    actor_type="manual",
                    source="console_hotkey",
                    target_type="order_request",
                    target_id=order_request["symbol"],
                    symbol=order_request["symbol"],
                    new_value=order_request,
                    status="started",
                    message="Console manual live order requested",
                    correlation_id=correlation_id,
                )
                if private_client is None:
                    notice = "Manual live order requires private API credentials"
                    notice_lines = [
                        "Set BITKUB_API_KEY and BITKUB_API_SECRET in .env before using live order features."
                    ]
                    audit_event(
                        action_type="manual_order",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="order_request",
                        target_id=order_request["symbol"],
                        symbol=order_request["symbol"],
                        new_value=order_request,
                        status="failed",
                        message=notice,
                        reason="missing_private_api_credentials",
                        correlation_id=correlation_id,
                    )
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
                    audit_event(
                        action_type="manual_order",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="order_request",
                        target_id=order_request["symbol"],
                        symbol=order_request["symbol"],
                        new_value=order_request,
                        status="failed",
                        message=notice,
                        reason="; ".join(notice_lines),
                        correlation_id=correlation_id,
                    )
                    notify_telegram("manual_live_order", notice, notice_lines)
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
                    audit_event(
                        action_type="manual_order",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="order_request",
                        target_id=order_request["symbol"],
                        symbol=order_request["symbol"],
                        new_value=order_request,
                        status="cancelled",
                        message=notice,
                        correlation_id=correlation_id,
                    )
                    return True

                if shadow_live_mode:
                    try:
                        manual_request = build_manual_live_order_request(
                            config=config,
                            rules=rules,
                        )
                        manual_errors = validate_manual_live_order_guardrails(
                            request=manual_request,
                            guardrails=live_guardrails,
                            available_balances=extract_available_balances(account_snapshot),
                        )
                        if manual_errors:
                            raise LiveExecutionGuardrailError("; ".join(manual_errors))
                    except LiveExecutionGuardrailError as e:
                        notice = "Manual shadow-live order blocked by guardrails"
                        notice_lines = str(e).split("; ")
                        record_trade_journal(
                            channel="manual_live_order",
                            status="blocked",
                            symbol=str(manual_order.get("symbol") or ""),
                            side=str(manual_order.get("side") or ""),
                            request_rate=float(manual_order.get("rate", 0.0) or 0.0),
                            amount_thb=float(manual_order.get("amount_thb", 0.0) or 0.0),
                            amount_coin=float(manual_order.get("amount_coin", 0.0) or 0.0),
                            latest_price=float(manual_order.get("rate", 0.0) or 0.0),
                            details={
                                "errors": notice_lines,
                                "guardrails": live_guardrails,
                                "shadow_live": True,
                            },
                        )
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type="manual_live_order",
                            severity="warning",
                            message=notice,
                            details={"errors": notice_lines, "guardrails": live_guardrails, "shadow_live": True},
                        )
                        audit_event(
                            action_type="manual_order",
                            actor_type="manual",
                            source="console_hotkey",
                            target_type="order_request",
                            target_id=order_request["symbol"],
                            symbol=order_request["symbol"],
                            new_value=order_request,
                            status="failed",
                            message=notice,
                            reason="; ".join(notice_lines),
                            correlation_id=correlation_id,
                            metadata={"guardrails": live_guardrails, "shadow_live": True},
                        )
                        return True

                    journal_id = record_trade_journal(
                        channel="manual_live_order",
                        status="shadow_recorded",
                        symbol=str(manual_request["symbol"]),
                        side=str(manual_request["side"]),
                        request_rate=float(manual_request["rate"]),
                        amount_thb=float(manual_order.get("amount_thb", 0.0) or 0.0),
                        amount_coin=float(manual_order.get("amount_coin", 0.0) or 0.0),
                        latest_price=float(manual_request["rate"]),
                        details={
                            "guardrails": live_guardrails,
                            "request_payload": manual_request["request_payload"],
                            "requested_notional_thb": manual_request["requested_notional_thb"],
                            "shadow_live": True,
                        },
                    )
                    notice = "Manual shadow-live order recorded"
                    notice_lines = [
                        f"journal_id={journal_id} symbol={manual_request['symbol']} side={manual_request['side']}",
                        "No exchange order was sent.",
                    ]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="manual_live_order",
                        severity="info",
                        message=notice,
                        details={
                            "journal_id": journal_id,
                            "symbol": manual_request["symbol"],
                            "side": manual_request["side"],
                            "shadow_live": True,
                        },
                    )
                    audit_event(
                        action_type="manual_order",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="trade_journal",
                        target_id=str(journal_id),
                        symbol=manual_request["symbol"],
                        new_value={
                            "request": order_request,
                            "journal_id": journal_id,
                            "shadow_live": True,
                        },
                        status="succeeded",
                        message=notice,
                        correlation_id=correlation_id,
                        metadata={"guardrails": live_guardrails},
                    )
                    notify_telegram(
                        "manual_live_order",
                        notice,
                        notice_lines,
                        payload={
                            "journal_id": journal_id,
                            "symbol": manual_request["symbol"],
                            "side": manual_request["side"],
                            "shadow_live": True,
                        },
                    )
                    return True

                try:
                    order_record, order_events = submit_manual_live_order(
                        client=private_client,
                        config=config,
                        rules=rules,
                        guardrails=live_guardrails,
                        available_balances=extract_available_balances(account_snapshot),
                        created_at=now_text(),
                        correlation_id=correlation_id,
                    )
                except LiveExecutionGuardrailError as e:
                    notice = "Manual live order blocked by guardrails"
                    notice_lines = str(e).split("; ")
                    record_trade_journal(
                        channel="manual_live_order",
                        status="blocked",
                        symbol=str(manual_order.get("symbol") or ""),
                        side=str(manual_order.get("side") or ""),
                        request_rate=float(manual_order.get("rate", 0.0) or 0.0),
                        amount_thb=float(manual_order.get("amount_thb", 0.0) or 0.0),
                        amount_coin=float(manual_order.get("amount_coin", 0.0) or 0.0),
                        latest_price=float(manual_order.get("rate", 0.0) or 0.0),
                        details={"errors": notice_lines, "guardrails": live_guardrails},
                    )
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="manual_live_order",
                        severity="warning",
                        message=notice,
                        details={"errors": notice_lines, "guardrails": live_guardrails},
                    )
                    audit_event(
                        action_type="manual_order",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="order_request",
                        target_id=order_request["symbol"],
                        symbol=order_request["symbol"],
                        new_value=order_request,
                        status="failed",
                        message=notice,
                        reason="; ".join(notice_lines),
                        correlation_id=correlation_id,
                        metadata={"guardrails": live_guardrails},
                    )
                    notify_telegram(
                        "manual_live_order",
                        notice,
                        notice_lines,
                        payload={"guardrails": live_guardrails},
                    )
                    return True
                except Exception as e:
                    notice = "Manual live order submission failed"
                    notice_lines = [str(e)]
                    record_trade_journal(
                        channel="manual_live_order",
                        status="failed",
                        symbol=str(manual_order.get("symbol") or ""),
                        side=str(manual_order.get("side") or ""),
                        request_rate=float(manual_order.get("rate", 0.0) or 0.0),
                        amount_thb=float(manual_order.get("amount_thb", 0.0) or 0.0),
                        amount_coin=float(manual_order.get("amount_coin", 0.0) or 0.0),
                        latest_price=float(manual_order.get("rate", 0.0) or 0.0),
                        details={"errors": notice_lines},
                    )
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="manual_live_order",
                        severity="error",
                        message=notice,
                        details={"errors": notice_lines},
                    )
                    audit_event(
                        action_type="manual_order",
                        actor_type="manual",
                        source="console_hotkey",
                        target_type="order_request",
                        target_id=order_request["symbol"],
                        symbol=order_request["symbol"],
                        new_value=order_request,
                        status="failed",
                        message=notice,
                        reason="; ".join(notice_lines),
                        correlation_id=correlation_id,
                    )
                    notify_telegram("manual_live_order", notice, notice_lines)
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
                record_trade_journal(
                    channel="manual_live_order",
                    status="live_submitted",
                    symbol=order_record["symbol"],
                    side=order_record["side"],
                    request_rate=float(order_record["request_payload"].get("rat", 0.0) or 0.0),
                    amount_thb=float(order_record["request_payload"].get("amt", 0.0) or 0.0)
                    if str(order_record["side"]) == "buy"
                    else None,
                    amount_coin=float(order_record["request_payload"].get("amt", 0.0) or 0.0)
                    if str(order_record["side"]) == "sell"
                    else None,
                    latest_price=float(order_record["request_payload"].get("rat", 0.0) or 0.0),
                    details={
                        "execution_order_id": execution_order_id,
                        "state": order_record["state"],
                        "guardrails": order_record.get("guardrails"),
                    },
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
                audit_event(
                    action_type="manual_order",
                    actor_type="manual",
                    source="console_hotkey",
                    target_type="execution_order",
                    target_id=str(execution_order_id),
                    symbol=order_record["symbol"],
                    new_value={
                        "request": order_request,
                        "execution_order_id": execution_order_id,
                        "state": order_record["state"],
                        "exchange_order_id": order_record.get("exchange_order_id"),
                    },
                    status="succeeded",
                    message=notice,
                    correlation_id=correlation_id,
                    metadata={"guardrails": order_record.get("guardrails")},
                )
                notify_telegram(
                    "manual_live_order",
                    notice,
                    notice_lines,
                    payload={
                        "execution_order_id": execution_order_id,
                        "symbol": order_record["symbol"],
                        "side": order_record["side"],
                        "state": order_record["state"],
                    },
                )
                sync_selected_execution_order()
                return True

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

                if shadow_live_mode:
                    try:
                        shadow_request = build_live_sell_request(
                            symbol=str(candidate["symbol"]),
                            amount_coin=float(candidate["amount_coin"]),
                            rate=float(candidate["rate"]),
                        )
                        shadow_errors = validate_live_sell_request_guardrails(
                            request=shadow_request,
                            guardrails=live_guardrails,
                            available_balances=available_balances,
                            latest_price=float(candidate["latest_price"]),
                        )
                        if shadow_errors:
                            raise LiveExecutionGuardrailError("; ".join(shadow_errors))
                    except LiveExecutionGuardrailError as e:
                        notice = "Auto shadow-live exit blocked by guardrails"
                        notice_lines = str(e).split("; ")
                        record_trade_journal(
                            channel="auto_live_exit",
                            status="blocked",
                            symbol=str(candidate["symbol"]),
                            side="sell",
                            exit_reason=str(candidate["exit_reason"]),
                            request_rate=float(candidate["rate"]),
                            latest_price=float(candidate["latest_price"]),
                            amount_coin=float(candidate["amount_coin"]),
                            details={
                                "errors": notice_lines,
                                "guardrails": live_guardrails,
                                "candidate": candidate,
                                "shadow_live": True,
                            },
                        )
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type="auto_live_exit",
                            severity="warning",
                            message=notice,
                            details={
                                "symbol": candidate["symbol"],
                                "exit_reason": candidate["exit_reason"],
                                "errors": notice_lines,
                                "shadow_live": True,
                            },
                        )
                        notify_telegram(
                            "auto_live_exit",
                            notice,
                            [f"symbol={candidate['symbol']}"] + notice_lines,
                            payload={"symbol": candidate["symbol"], "exit_reason": candidate["exit_reason"], "shadow_live": True},
                        )
                        return False

                    journal_id = record_trade_journal(
                        channel="auto_live_exit",
                        status="shadow_recorded",
                        symbol=str(candidate["symbol"]),
                        side="sell",
                        exit_reason=str(candidate["exit_reason"]),
                        request_rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        amount_coin=float(candidate["amount_coin"]),
                        details={
                            "guardrails": live_guardrails,
                            "candidate": candidate,
                            "request_payload": shadow_request["request_payload"],
                            "shadow_live": True,
                        },
                    )
                    notice = "Auto shadow-live exit recorded"
                    notice_lines = [
                        f"journal_id={journal_id} symbol={candidate['symbol']} reason={candidate['exit_reason']}",
                        f"amount={float(candidate['amount_coin']):,.8f} rate={float(candidate['rate']):,.8f} latest={float(candidate['latest_price']):,.8f}",
                        "No exchange order was sent.",
                    ]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="auto_live_exit",
                        severity="info",
                        message=notice,
                        details={
                            "journal_id": journal_id,
                            "symbol": candidate["symbol"],
                            "exit_reason": candidate["exit_reason"],
                            "shadow_live": True,
                        },
                    )
                    notify_telegram(
                        "auto_live_exit",
                        notice,
                        notice_lines,
                        payload={
                            "journal_id": journal_id,
                            "symbol": candidate["symbol"],
                            "exit_reason": candidate["exit_reason"],
                            "shadow_live": True,
                        },
                    )
                    return True

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
                    record_trade_journal(
                        channel="auto_live_exit",
                        status="blocked",
                        symbol=str(candidate["symbol"]),
                        side="sell",
                        exit_reason=str(candidate["exit_reason"]),
                        request_rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        amount_coin=float(candidate["amount_coin"]),
                        details={
                            "errors": notice_lines,
                            "guardrails": live_guardrails,
                            "candidate": candidate,
                        },
                    )
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
                    notify_telegram(
                        "auto_live_exit",
                        notice,
                        [f"symbol={candidate['symbol']}"] + notice_lines,
                        payload={"symbol": candidate["symbol"], "exit_reason": candidate["exit_reason"]},
                    )
                    return False
                except Exception as e:
                    notice = "Auto live exit submission failed"
                    notice_lines = [str(e)]
                    record_trade_journal(
                        channel="auto_live_exit",
                        status="failed",
                        symbol=str(candidate["symbol"]),
                        side="sell",
                        exit_reason=str(candidate["exit_reason"]),
                        request_rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        amount_coin=float(candidate["amount_coin"]),
                        details={"errors": notice_lines, "candidate": candidate},
                    )
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
                    notify_telegram(
                        "auto_live_exit",
                        notice,
                        [f"symbol={candidate['symbol']}", str(e)],
                        payload={"symbol": candidate["symbol"], "exit_reason": candidate["exit_reason"]},
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
                record_trade_journal(
                    channel="auto_live_exit",
                    status="live_submitted",
                    symbol=order_record["symbol"],
                    side=order_record["side"],
                    exit_reason=str(candidate["exit_reason"]),
                    request_rate=float(candidate["rate"]),
                    latest_price=float(candidate["latest_price"]),
                    amount_coin=float(candidate["amount_coin"]),
                    details={
                        "execution_order_id": execution_order_id,
                        "state": order_record["state"],
                        "guardrails": order_record.get("guardrails"),
                    },
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
                notify_telegram(
                    "auto_live_exit",
                    notice,
                    notice_lines,
                    payload={
                        "execution_order_id": execution_order_id,
                        "symbol": order_record["symbol"],
                        "state": order_record["state"],
                        "exit_reason": candidate["exit_reason"],
                    },
                )
                return True

            def submit_auto_live_entry_action(candidate: dict) -> bool:
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

                if shadow_live_mode:
                    try:
                        shadow_request = build_live_buy_request(
                            symbol=str(candidate["symbol"]),
                            amount_thb=float(candidate["amount_thb"]),
                            rate=float(candidate["rate"]),
                        )
                        shadow_errors = validate_live_buy_request_guardrails(
                            request=shadow_request,
                            guardrails=live_guardrails,
                            available_balances=available_balances,
                            latest_price=float(candidate["latest_price"]),
                        )
                        if shadow_errors:
                            raise LiveExecutionGuardrailError("; ".join(shadow_errors))
                    except LiveExecutionGuardrailError as e:
                        notice = "Auto shadow-live entry blocked by guardrails"
                        notice_lines = str(e).split("; ")
                        record_trade_journal(
                            channel="auto_live_entry",
                            status="blocked",
                            symbol=str(candidate["symbol"]),
                            side="buy",
                            signal_reason=str(candidate["signal_reason"]),
                            request_rate=float(candidate["rate"]),
                            latest_price=float(candidate["latest_price"]),
                            amount_thb=float(candidate["amount_thb"]),
                            details={
                                "errors": notice_lines,
                                "guardrails": live_guardrails,
                                "candidate": candidate,
                                "shadow_live": True,
                            },
                        )
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type="auto_live_entry",
                            severity="warning",
                            message=notice,
                            details={
                                "symbol": candidate["symbol"],
                                "signal_reason": candidate["signal_reason"],
                                "errors": notice_lines,
                                "shadow_live": True,
                            },
                        )
                        notify_telegram(
                            "auto_live_entry",
                            notice,
                            [f"symbol={candidate['symbol']}"] + notice_lines,
                            payload={
                                "symbol": candidate["symbol"],
                                "signal_reason": candidate["signal_reason"],
                                "shadow_live": True,
                            },
                        )
                        return False

                    journal_id = record_trade_journal(
                        channel="auto_live_entry",
                        status="shadow_recorded",
                        symbol=str(candidate["symbol"]),
                        side="buy",
                        signal_reason=str(candidate["signal_reason"]),
                        request_rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        amount_thb=float(candidate["amount_thb"]),
                        details={
                            "guardrails": live_guardrails,
                            "candidate": candidate,
                            "request_payload": shadow_request["request_payload"],
                            "shadow_live": True,
                        },
                    )
                    notice = "Auto shadow-live entry recorded"
                    notice_lines = [
                        f"journal_id={journal_id} symbol={candidate['symbol']} reason={candidate['signal_reason']}",
                        f"amount_thb={float(candidate['amount_thb']):,.2f} rate={float(candidate['rate']):,.8f} latest={float(candidate['latest_price']):,.8f}",
                        "No exchange order was sent.",
                    ]
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="auto_live_entry",
                        severity="info",
                        message=notice,
                        details={
                            "journal_id": journal_id,
                            "symbol": candidate["symbol"],
                            "signal_reason": candidate["signal_reason"],
                            "shadow_live": True,
                        },
                    )
                    notify_telegram(
                        "auto_live_entry",
                        notice,
                        notice_lines,
                        payload={
                            "journal_id": journal_id,
                            "symbol": candidate["symbol"],
                            "signal_reason": candidate["signal_reason"],
                            "shadow_live": True,
                        },
                    )
                    return True

                try:
                    order_record, order_events = submit_auto_live_entry_order(
                        client=private_client,
                        symbol=str(candidate["symbol"]),
                        amount_thb=float(candidate["amount_thb"]),
                        rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        signal_reason=str(candidate["signal_reason"]),
                        guardrails=live_guardrails,
                        available_balances=available_balances,
                        created_at=now_text(),
                    )
                except LiveExecutionGuardrailError as e:
                    notice = "Auto live entry blocked by guardrails"
                    notice_lines = str(e).split("; ")
                    record_trade_journal(
                        channel="auto_live_entry",
                        status="blocked",
                        symbol=str(candidate["symbol"]),
                        side="buy",
                        signal_reason=str(candidate["signal_reason"]),
                        request_rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        amount_thb=float(candidate["amount_thb"]),
                        details={
                            "errors": notice_lines,
                            "guardrails": live_guardrails,
                            "candidate": candidate,
                        },
                    )
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="auto_live_entry",
                        severity="warning",
                        message=notice,
                        details={
                            "symbol": candidate["symbol"],
                            "signal_reason": candidate["signal_reason"],
                            "errors": notice_lines,
                        },
                    )
                    notify_telegram(
                        "auto_live_entry",
                        notice,
                        [f"symbol={candidate['symbol']}"] + notice_lines,
                        payload={"symbol": candidate["symbol"], "signal_reason": candidate["signal_reason"]},
                    )
                    return False
                except Exception as e:
                    error_text = str(e)
                    if is_unsupported_symbol_error_message(error_text):
                        newly_blocked = remember_unsupported_live_entry_symbol(
                            symbol=str(candidate["symbol"]),
                            error_message=error_text,
                        )
                        notice = "Auto live entry disabled for unsupported symbol"
                        notice_lines = [
                            f"symbol={candidate['symbol']}",
                            error_text,
                        ]
                        record_trade_journal(
                            channel="auto_live_entry",
                            status="unsupported_symbol",
                            symbol=str(candidate["symbol"]),
                            side="buy",
                            signal_reason=str(candidate["signal_reason"]),
                            request_rate=float(candidate["rate"]),
                            latest_price=float(candidate["latest_price"]),
                            amount_thb=float(candidate["amount_thb"]),
                            details={
                                "errors": notice_lines,
                                "candidate": candidate,
                                "newly_blocked": newly_blocked,
                                "error_category": "unsupported_symbol",
                            },
                        )
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type="auto_live_entry",
                            severity="warning",
                            message=notice,
                            details={
                                "symbol": candidate["symbol"],
                                "signal_reason": candidate["signal_reason"],
                                "errors": notice_lines,
                                "newly_blocked": newly_blocked,
                            },
                        )
                        if newly_blocked:
                            notify_telegram(
                                "auto_live_entry",
                                notice,
                                notice_lines,
                                payload={
                                    "symbol": candidate["symbol"],
                                    "signal_reason": candidate["signal_reason"],
                                },
                            )
                        return False

                    notice = "Auto live entry submission failed"
                    notice_lines = [error_text]
                    record_trade_journal(
                        channel="auto_live_entry",
                        status="failed",
                        symbol=str(candidate["symbol"]),
                        side="buy",
                        signal_reason=str(candidate["signal_reason"]),
                        request_rate=float(candidate["rate"]),
                        latest_price=float(candidate["latest_price"]),
                        amount_thb=float(candidate["amount_thb"]),
                        details={"errors": notice_lines, "candidate": candidate},
                    )
                    insert_runtime_event(
                        created_at=now_text(),
                        event_type="auto_live_entry",
                        severity="error",
                        message=notice,
                        details={
                            "symbol": candidate["symbol"],
                            "signal_reason": candidate["signal_reason"],
                            "errors": notice_lines,
                        },
                    )
                    notify_telegram(
                        "auto_live_entry",
                        notice,
                        [f"symbol={candidate['symbol']}", error_text],
                        payload={"symbol": candidate["symbol"], "signal_reason": candidate["signal_reason"]},
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
                record_trade_journal(
                    channel="auto_live_entry",
                    status="live_submitted",
                    symbol=order_record["symbol"],
                    side=order_record["side"],
                    signal_reason=str(candidate["signal_reason"]),
                    request_rate=float(candidate["rate"]),
                    latest_price=float(candidate["latest_price"]),
                    amount_thb=float(candidate["amount_thb"]),
                    details={
                        "execution_order_id": execution_order_id,
                        "state": order_record["state"],
                        "guardrails": order_record.get("guardrails"),
                        "request_payload": order_record["request_payload"],
                    },
                )

                notice = "Auto live entry order submitted"
                notice_lines = [
                    f"id={execution_order_id} symbol={order_record['symbol']} reason={candidate['signal_reason']} state={order_record['state']}",
                    f"amount_thb={float(candidate['amount_thb']):,.2f} rate={float(candidate['rate']):,.8f} latest={float(candidate['latest_price']):,.8f}",
                ]
                if order_record.get("exchange_order_id"):
                    notice_lines.append(
                        f"exchange_order_id={order_record['exchange_order_id']}"
                    )
                insert_runtime_event(
                    created_at=now_text(),
                    event_type="auto_live_entry",
                    severity="warning",
                    message=notice,
                    details={
                        "execution_order_id": execution_order_id,
                        "symbol": order_record["symbol"],
                        "state": order_record["state"],
                        "signal_reason": candidate["signal_reason"],
                    },
                )
                notify_telegram(
                    "auto_live_entry",
                    notice,
                    notice_lines,
                    payload={
                        "execution_order_id": execution_order_id,
                        "symbol": order_record["symbol"],
                        "state": order_record["state"],
                        "signal_reason": candidate["signal_reason"],
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

            def cancel_specific_live_order(target_order: dict, *, source: str):
                nonlocal account_snapshot, notice, notice_lines, private_api_status, private_api_capabilities
                execution_order_id = int(target_order["id"])
                correlation_id = new_correlation_id("live_order_cancel")
                audit_event(
                    action_type="live_order_cancel",
                    actor_type=audit_actor_type_from_source(source),
                    source=source,
                    target_type="execution_order",
                    target_id=str(execution_order_id),
                    symbol=str(target_order.get("symbol") or ""),
                    old_value={
                        "execution_order_id": execution_order_id,
                        "symbol": target_order.get("symbol"),
                        "side": target_order.get("side"),
                        "state": target_order.get("state"),
                    },
                    status="started",
                    message="Live order cancel requested",
                    correlation_id=correlation_id,
                )

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
                        details={"errors": notice_lines, "source": source},
                    )
                    audit_event(
                        action_type="live_order_cancel",
                        actor_type=audit_actor_type_from_source(source),
                        source=source,
                        target_type="execution_order",
                        target_id=str(execution_order_id),
                        symbol=str(target_order.get("symbol") or ""),
                        old_value={
                            "execution_order_id": execution_order_id,
                            "symbol": target_order.get("symbol"),
                            "side": target_order.get("side"),
                            "state": target_order.get("state"),
                        },
                        status="failed",
                        message=notice,
                        reason="; ".join(notice_lines),
                        correlation_id=correlation_id,
                    )
                    return False, notice, notice_lines

                snapshot_errors = account_snapshot_errors(account_snapshot)
                private_api_capabilities = summarize_account_capabilities(account_snapshot)
                private_api_status = (
                    "wallet/balance ready, some order endpoints unavailable"
                    if snapshot_errors
                    else "wallet/balance/open-orders ready"
                )
                insert_account_snapshot(
                    created_at=now_text(),
                    source=source,
                    private_api_status=private_api_status,
                    capabilities=private_api_capabilities,
                    snapshot=account_snapshot,
                )

                try:
                    canceled_record, cancel_events = cancel_live_order(
                        client=private_client,
                        order_record=target_order,
                        occurred_at=now_text(),
                        correlation_id=correlation_id,
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
                            "source": source,
                        },
                    )
                    audit_event(
                        action_type="live_order_cancel",
                        actor_type=audit_actor_type_from_source(source),
                        source=source,
                        target_type="execution_order",
                        target_id=str(execution_order_id),
                        symbol=str(target_order.get("symbol") or ""),
                        old_value={
                            "execution_order_id": execution_order_id,
                            "symbol": target_order.get("symbol"),
                            "side": target_order.get("side"),
                            "state": target_order.get("state"),
                        },
                        status="failed",
                        message=notice,
                        reason="; ".join(notice_lines),
                        correlation_id=correlation_id,
                    )
                    return False, notice, notice_lines

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
                        "source": source,
                    },
                )
                audit_event(
                    action_type="live_order_cancel",
                    actor_type=audit_actor_type_from_source(source),
                    source=source,
                    target_type="execution_order",
                    target_id=str(execution_order_id),
                    symbol=canceled_record["symbol"],
                    old_value={
                        "execution_order_id": execution_order_id,
                        "symbol": target_order.get("symbol"),
                        "side": target_order.get("side"),
                        "state": target_order.get("state"),
                    },
                    new_value={
                        "execution_order_id": execution_order_id,
                        "symbol": canceled_record["symbol"],
                        "side": canceled_record["side"],
                        "state": canceled_record["state"],
                        "exchange_order_id": canceled_record.get("exchange_order_id"),
                    },
                    status="succeeded",
                    message=notice,
                    correlation_id=correlation_id,
                )
                sync_selected_execution_order()
                return True, notice, notice_lines

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

                cancel_specific_live_order(target_order, source="live_order_cancel_hotkey")
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
            mode_notice_text, mode_notice_lines = mode_notice(
                trading_mode,
                positions,
                strategy_execution_wired=strategy_execution_wired,
                live_auto_exit_enabled=live_auto_exit_enabled,
            )
            render_header(
                timestamp=timestamp,
                app_version_label=app_version_label,
                app_version_detail=app_version_detail,
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
                flush_telegram_notifications()
                process_telegram_commands()
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
            entry_signal_rows: list[dict] = []

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
                elif (
                    trading_mode in {"live", "shadow-live"}
                    and live_auto_entry_enabled
                    and changed
                    and current_zone == "BUY"
                ):
                    entry_signal_rows.append(
                        {
                            "symbol": symbol,
                            "latest_price": last_price,
                            "signal_reason": "BUY_ZONE_ENTRY",
                        }
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
                    if live_auto_entry_enabled:
                        print(
                            "Live trading active: guarded strategy-driven entries are enabled for config rules."
                        )
                        print(
                            "Market scan continues and one guarded live buy may be submitted per loop when a fresh BUY zone entry appears."
                        )
                    elif live_auto_exit_enabled:
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
                elif trading_mode == "shadow-live":
                    if live_auto_entry_enabled:
                        print(
                            "Shadow-live mode active: guarded strategy-driven entry intents are recorded for config rules."
                        )
                        print(
                            "Market scan continues and one guarded shadow buy intent may be recorded per loop when a fresh BUY zone entry appears."
                        )
                    elif live_auto_exit_enabled:
                        print(
                            "Shadow-live foundation active: entry intents remain disconnected, but auto shadow exit is enabled."
                        )
                        print(
                            "Market scan continues and exchange holdings may record guarded sell intents when exit conditions trigger."
                        )
                    else:
                        print(
                            "Shadow-live foundation active: guardrails are loaded, but no shadow trade intents are being recorded in this build."
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

            if trading_mode in {"live", "shadow-live"} and private_client is not None:
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

                if (live_auto_exit_enabled or live_auto_entry_enabled) and not manual_pause and not safety_pause:
                    try:
                        account_snapshot = fetch_account_snapshot(private_client)
                        track_unsupported_live_entry_symbols_from_snapshot(account_snapshot)
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
                        current_open_execution_orders = fetch_open_execution_orders()
                        exchange_open_orders_by_symbol = extract_open_orders_by_symbol(
                            account_snapshot
                        )
                        live_order_submitted = False

                        if live_auto_exit_enabled:
                            exit_candidates = evaluate_live_exit_candidates(
                                rules=rules,
                                live_holdings_rows=live_holdings_rows,
                                open_execution_orders=current_open_execution_orders,
                                exchange_open_orders_by_symbol=exchange_open_orders_by_symbol,
                            )
                            if exit_candidates:
                                live_order_submitted = submit_auto_live_exit_action(exit_candidates[0])

                        if live_auto_entry_enabled and not live_order_submitted:
                            entry_review = evaluate_live_entry_candidates(
                                config=config,
                                rules=rules,
                                entry_signal_rows=entry_signal_rows,
                                live_holdings_rows=live_holdings_rows,
                                open_execution_orders=current_open_execution_orders,
                                exchange_open_orders_by_symbol=exchange_open_orders_by_symbol,
                                unsupported_symbols=unsupported_live_entry_symbols,
                            )
                            entry_candidates = list(entry_review.get("candidates", []))
                            rejected_entry_candidates = list(entry_review.get("rejected", []))
                            ranking_errors = list(entry_review.get("ranking_errors", []))
                            if entry_candidates or rejected_entry_candidates or ranking_errors:
                                insert_runtime_event(
                                    created_at=now_text(),
                                    event_type="auto_live_entry_review",
                                    severity="info",
                                    message="Auto live entry candidate review completed",
                                    details={
                                        "candidates": [
                                            {
                                                "symbol": row.get("symbol"),
                                                "ranking_score": row.get("ranking_score"),
                                                "trend_bias": row.get("trend_bias"),
                                                "signal_reason": row.get("signal_reason"),
                                                "entry_discount_percent": row.get("entry_discount_percent"),
                                            }
                                            for row in entry_candidates[:5]
                                        ],
                                        "rejected": [
                                            {
                                                "symbol": row.get("symbol"),
                                                "ranking_score": row.get("ranking_score"),
                                                "trend_bias": row.get("trend_bias"),
                                                "reasons": row.get("reasons"),
                                            }
                                            for row in rejected_entry_candidates[:8]
                                        ],
                                        "ranking_errors": ranking_errors[:8],
                                        "ranking_context": entry_review.get("ranking_context", {}),
                                    },
                                )
                            if entry_candidates:
                                submit_auto_live_entry_action(entry_candidates[0])
                    except Exception as e:
                        event_type = "auto_live_entry" if live_auto_entry_enabled else "auto_live_exit"
                        message = (
                            "Auto live entry evaluation failed"
                            if live_auto_entry_enabled
                            else "Auto live exit evaluation failed"
                        )
                        insert_runtime_event(
                            created_at=now_text(),
                            event_type=event_type,
                            severity="error",
                            message=message,
                            details={"exception": str(e)},
                        )
                        notify_telegram(
                            event_type,
                            message,
                            [str(e)],
                            payload={"exception": str(e)},
                        )

            if trading_mode in {"live", "shadow-live"}:
                run_state_reconciliation(source="periodic", force=False)

            flush_telegram_notifications()
            process_telegram_commands()

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
                shutdown_reason = "operator_quit"
                break

        except KeyboardInterrupt:
            persist_state()
            shutdown_reason = "keyboard_interrupt"
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
            notify_telegram(
                "runtime_error",
                notice,
                [str(e)],
                payload={"exception": str(e)},
            )
            print(f"\nRuntime error: {e}")
            time.sleep(5)

    audit_event(
        action_type="runtime_shutdown",
        actor_type="system" if shutdown_reason == "runtime_stop" else "manual",
        source=shutdown_reason,
        target_type="engine",
        target_id="main",
        status="succeeded",
        message="Bitkub engine shutdown completed",
        correlation_id=runtime_run_id,
        metadata={"reason": shutdown_reason},
    )


if __name__ == "__main__":
    main()
