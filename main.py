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
from core.trade_engine import handle_symbol
from services.account_service import (
    account_snapshot_errors,
    fetch_account_snapshot,
    summarize_account_capabilities,
)
from services.db_service import (
    DB_PATH,
    fetch_dashboard_summary,
    init_db,
    insert_account_snapshot,
    insert_market_snapshot,
    insert_reconciliation_result,
    insert_runtime_event,
)
from services.log_service import (
    ensure_signal_log_file,
    ensure_trade_log_file,
    write_signal_log,
)
from services.reconciliation_service import (
    extract_available_balances,
    reconcile_positions_with_balances,
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
    print_health_snapshot,
    print_market_table,
    print_open_positions_snapshot,
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
    if mode == "live-disabled":
        return "Trading engine is locked because live mode is disabled in this build"
    return None


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
    notice: str | None = None
    notice_lines: list[str] | None = restore_messages or None
    private_api_status = "not configured"
    private_api_capabilities: list[str] | None = None

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
        safety_pause = True
        safety_pause_lines = startup_reconciliation_warnings + [
            "Read-only reconciliation detected a mismatch between local positions and exchange balances."
        ]
        notice = "Safety pause: startup reconciliation mismatch detected"
        notice_lines = safety_pause_lines
        insert_runtime_event(
            created_at=now_text(),
            event_type="reconciliation",
            severity="warning",
            message=notice,
            details={"lines": safety_pause_lines},
        )

    def persist_state():
        save_runtime_state(
            last_zones,
            positions,
            daily_stats,
            cooldowns,
            manual_pause=manual_pause,
        )

    persist_state()

    while True:
        try:
            rules = config["rules"]
            trading_mode = str(config.get("mode", "paper"))
            trading_enabled = trading_mode == "paper"
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
                nonlocal config, notice, notice_lines
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
                change_lines = summarize_config_changes(old_config, new_config)
                if safety_pause:
                    clear_safety_pause()
                    notice = "Reloaded config.json successfully; safety pause cleared"
                else:
                    notice = "Reloaded config.json successfully"
                notice_lines = change_lines
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
                return True

            def show_daily_stats_action():
                today = today_key()
                print_daily_stats_snapshot(today=today, today_stats=daily_stats.get(today, {}))
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
                return True

            def show_database_summary_action():
                summary = fetch_dashboard_summary(today=today_key())
                print_database_summary(summary, today=today_key())
                return True

            def show_health_action():
                summary = fetch_dashboard_summary(today=today_key())
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
                    "private_api_status": private_api_status,
                    "private_api_capabilities": private_api_capabilities or [],
                    "latest_account_snapshot": summary.get("latest_account_snapshot"),
                    "latest_reconciliation": summary.get("latest_reconciliation"),
                    "notice": notice or mode_notice_text,
                    "notice_lines": notice_lines or mode_notice_lines,
                }
                print_health_snapshot(health)
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
                "h": show_health_action,
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
                    "A=account, B=db summary, H=health, Q=quit)"
                )

                should_continue = wait_with_hotkeys(interval_seconds, hotkey_actions)
                if not should_continue:
                    break
                continue

            ticker = get_ticker()
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
            print(
                f"Waiting {interval_seconds}s for next refresh... "
                "(R=reload, P=manual pause, S=positions, D=daily stats, "
                "A=account, B=db summary, H=health, Q=quit)"
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
