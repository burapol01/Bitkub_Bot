import os
import shutil

from core.strategy import price_change_percent

SYMBOL_COL = 12
PRICE_COL = 18
ZONE_COL = 8


def terminal_width() -> int:
    width = shutil.get_terminal_size((140, 40)).columns
    return max(width, 100)


def divider(char: str = "-") -> str:
    return char * terminal_width()


def clear_screen():
    os.system("cls")


def format_bytes(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    size = float(size_bytes)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.2f} {units[unit_index]}"


def section_title(title: str):
    print()
    print(title)
    print(divider("-"))


def pause_mode_text(manual_pause: bool, safety_pause: bool) -> str:
    if safety_pause:
        return "SAFETY PAUSE"
    if manual_pause:
        return "MANUAL PAUSE"
    return "RUNNING"


def pause_hotkey_text(manual_pause: bool, safety_pause: bool) -> str:
    if safety_pause:
        return "blocked by safety"
    if manual_pause:
        return "manual resume"
    return "manual pause"


def render_header(
    *,
    timestamp: str,
    app_version_label: str,
    app_version_detail: str,
    trading_mode: str,
    fee_rate: float,
    interval_seconds: int,
    manual_pause: bool,
    safety_pause: bool,
    notice: str | None,
    notice_lines: list[str] | None,
    open_positions_count: int,
    tracked_symbols_today: int,
    active_cooldowns_count: int,
    total_trades: int,
    total_wins: int,
    total_losses: int,
    total_pnl: float,
    private_api_status: str,
    private_api_capabilities: list[str] | None,
):
    clear_screen()
    status_line = (
        f"Time: {timestamp} | State: {pause_mode_text(manual_pause, safety_pause)} | "
        f"Trading: {trading_mode.upper()} | "
        f"Fee: {fee_rate * 100:.2f}% per side | Interval: {interval_seconds}s"
    )
    runtime_line = (
        f"Open positions: {open_positions_count} | "
        f"Tracked symbols today: {tracked_symbols_today} | "
        f"Active cooldowns: {active_cooldowns_count}"
    )
    totals_line = (
        f"Daily totals: trades={total_trades} wins={total_wins} "
        f"losses={total_losses} realized={total_pnl:,.2f} THB"
    )
    pause_line = (
        f"Pause flags: manual={'ON' if manual_pause else 'OFF'} | "
        f"safety={'ON' if safety_pause else 'OFF'}"
    )
    hotkey_line = (
        f"Hotkeys: R=reload | P={pause_hotkey_text(manual_pause, safety_pause)} | "
        f"S=pos | D=daily | A=acct | B=db | T=reports | "
        f"F=filter | H=health | O=orders | I=wallet | C=clear | M=manual | "
        f"L=live | E=exec | K=next | U=update | X=cancel | Q=quit"
    )

    print(divider("="))
    print("BITKUB PAPER TRADE TRACKER".center(terminal_width()))
    print(divider("="))
    print()
    print(status_line)
    print(runtime_line)
    print(totals_line)
    print(pause_line)
    if app_version_detail:
        print(f"Version: {app_version_label} | {app_version_detail}")
    else:
        print(f"Version: {app_version_label}")
    print(f"Private API: {private_api_status}")
    if private_api_capabilities:
        print(f"Capabilities: {' | '.join(private_api_capabilities)}")
    print(hotkey_line)
    if notice:
        print()
        print(f"Notice: {notice}")
    if notice_lines:
        print(divider("-"))
        for line in notice_lines:
            print(f"  {line}")
    print(divider("="))


def print_market_table(rows: list[dict]):
    section_title("MARKET OVERVIEW")
    print(
        f"{'SYMBOL':<{SYMBOL_COL}} "
        f"{'LAST':>{PRICE_COL}} "
        f"{'BUY <=':>{PRICE_COL}} "
        f"{'SELL >=':>{PRICE_COL}} "
        f"{'ZONE':>{ZONE_COL}}  STATUS"
    )
    print(divider("-"))

    for row in rows:
        print(
            f"{row['symbol']:<{SYMBOL_COL}} "
            f"{row['last_text']:>{PRICE_COL}} "
            f"{row['buy_below']:>{PRICE_COL}} "
            f"{row['sell_above']:>{PRICE_COL}} "
            f"{row['zone']:>{ZONE_COL}}  {row['status']}"
        )
        print(f"{'':<{SYMBOL_COL}} {row['detail']}")

    if not rows:
        print("No symbols configured.")

    print(divider("-"))


def position_detail_text(symbol: str, last_price: float, rule: dict, fee_rate: float, positions: dict) -> str:
    if symbol not in positions:
        return "position: none"

    pos = positions[symbol]
    position_fee_rate = float(pos.get("fee_rate", fee_rate))
    active_sell_above = float(pos.get("sell_above", rule["sell_above"]))
    stop_loss_percent = float(pos.get("stop_loss_percent", rule["stop_loss_percent"]))
    take_profit_percent = float(
        pos.get("take_profit_percent", rule["take_profit_percent"])
    )

    gross_value = last_price * float(pos["coin_qty"])
    estimated_sell_fee = gross_value * position_fee_rate
    net_value = gross_value - estimated_sell_fee
    unrealized_pnl = net_value - float(pos["budget_thb"])
    unrealized_pnl_percent = (
        unrealized_pnl / float(pos["budget_thb"]) * 100
        if float(pos["budget_thb"]) > 0
        else 0.0
    )
    move_percent = price_change_percent(float(pos["buy_price"]), last_price)
    entry_source = str(pos.get("entry_source", "strategy_buy"))
    source_text = (
        "wallet"
        if entry_source == "wallet_import"
        else "strategy"
        if entry_source == "strategy_buy"
        else entry_source
    )

    return (
        f"position: entry={pos['buy_price']:,.8f} qty={pos['coin_qty']:,.8f} "
        f"target={active_sell_above:,.8f} sl={stop_loss_percent:.2f}% "
        f"tp={take_profit_percent:.2f}% move={move_percent:.2f}% "
        f"uPnL={unrealized_pnl:,.2f} THB ({unrealized_pnl_percent:.2f}%) "
        f"src={source_text}"
    )


def print_open_positions_snapshot(
    positions: dict,
    latest_prices: dict[str, float],
    cooldown_rows: list[tuple[str, str, int]],
):
    print()
    print(divider("="))
    section_title("OPEN POSITIONS")

    if not positions:
        print("No open positions.")
    else:
        print(
            f"{'SYMBOL':<{SYMBOL_COL}} "
            f"{'ENTRY':>{PRICE_COL}} "
            f"{'LAST':>{PRICE_COL}} "
            f"{'TARGET':>{PRICE_COL}} "
            f"{'MOVE%':>8} {'UPNL THB':>14}  DETAILS"
        )
        print(divider("-"))

        for symbol, pos in positions.items():
            current_price = latest_prices.get(symbol)
            fee_rate = float(pos.get("fee_rate", 0.0))
            target = float(pos.get("sell_above", 0.0))
            move_percent = 0.0
            unrealized_pnl = 0.0
            last_text = "n/a"

            if current_price is not None:
                gross_value = current_price * float(pos["coin_qty"])
                estimated_sell_fee = gross_value * fee_rate
                net_value = gross_value - estimated_sell_fee
                unrealized_pnl = net_value - float(pos["budget_thb"])
                move_percent = price_change_percent(float(pos["buy_price"]), current_price)
                last_text = f"{current_price:,.8f}"

            print(
                f"{symbol:<{SYMBOL_COL}} "
                f"{float(pos['buy_price']):>{PRICE_COL},.8f} "
                f"{last_text:>{PRICE_COL}} "
                f"{target:>{PRICE_COL},.8f} "
                f"{move_percent:8.2f} {unrealized_pnl:14,.2f}  "
                f"qty={float(pos['coin_qty']):,.8f} sl={float(pos.get('stop_loss_percent', 0.0)):.2f}% "
                f"tp={float(pos.get('take_profit_percent', 0.0)):.2f}% "
                f"src={'wallet' if pos.get('entry_source') == 'wallet_import' else 'strategy'}"
            )

    section_title("ACTIVE COOLDOWNS")

    if not cooldown_rows:
        print("No active cooldowns.")
    else:
        for symbol, cooldown_until, remaining_seconds in cooldown_rows:
            print(
                f"{symbol:<{SYMBOL_COL}} until={cooldown_until} remaining={remaining_seconds}s"
            )

    print(divider("=") + "\n")


def print_live_holdings_snapshot(rows: list[dict[str, float | str | None]]):
    print()
    print(divider("="))
    section_title("LIVE HOLDINGS")

    if not rows:
        print("No tracked live holdings were found in the exchange balances.")
        print(divider("=") + "\n")
        return

    print(
        f"{'SYMBOL':<{SYMBOL_COL}} "
        f"{'AVAILABLE':>{PRICE_COL}} "
        f"{'RESERVED':>{PRICE_COL}} "
        f"{'LAST':>{PRICE_COL}} "
        f"{'VALUE THB':>{PRICE_COL}}  DETAILS"
    )
    print(divider("-"))

    for row in rows:
        latest_price = row.get("latest_price")
        market_value_thb = row.get("market_value_thb")
        last_price_text = f"{float(latest_price):,.8f}" if latest_price is not None else "n/a"
        market_value_text = (
            f"{float(market_value_thb):,.2f}" if market_value_thb is not None else "n/a"
        )
        detail_parts = [f"asset={row['asset']} total={float(row['total_qty']):,.8f}"]
        if row.get("last_execution_rate") is not None:
            detail_parts.append(
                f"last_exec={str(row.get('last_execution_side'))}@{float(row['last_execution_rate']):,.8f}"
            )
        if row.get("entry_rate") is not None:
            detail_parts.append(f"entry={float(row['entry_rate']):,.8f}")
        if row.get("stop_loss_price") is not None:
            detail_parts.append(f"sl={float(row['stop_loss_price']):,.8f}")
        if row.get("take_profit_price") is not None:
            detail_parts.append(f"tp={float(row['take_profit_price']):,.8f}")
        if row.get("sell_above") is not None:
            detail_parts.append(f"target={float(row['sell_above']):,.8f}")
        if row.get("auto_exit_status"):
            detail_parts.append(f"auto={row['auto_exit_status']}")
        if row.get("last_execution_order_id"):
            detail_parts.append(f"order_id={row['last_execution_order_id']}")

        print(
            f"{str(row['symbol']):<{SYMBOL_COL}} "
            f"{float(row['available_qty']):>{PRICE_COL},.8f} "
            f"{float(row['reserved_qty']):>{PRICE_COL},.8f} "
            f"{last_price_text:>{PRICE_COL}} "
            f"{market_value_text:>{PRICE_COL}}  "
            + " | ".join(detail_parts)
        )

    print(divider("=") + "\n")


def print_execution_orders_snapshot(summary: dict[str, list[dict]], selected_execution_order_id: int | None):
    print()
    print(divider("="))
    section_title("EXECUTION ORDERS")

    open_orders = summary.get("open_orders", [])
    recent_orders = summary.get("recent_orders", [])
    recent_events = summary.get("recent_events", [])
    live_reconciliation = summary.get("live_reconciliation", {})

    section_title("OPEN EXECUTION ORDERS")
    if not open_orders:
        print("No open execution orders.")
    else:
        for row in open_orders:
            marker = "*" if int(row["id"]) == int(selected_execution_order_id or -1) else " "
            print(
                f"{marker} id={row['id']} | {row['symbol']} | {row['side']} | "
                f"state={row['state']} | updated={row['updated_at']}"
            )
            if row.get("exchange_order_id"):
                print(f"  exchange_order_id={row['exchange_order_id']}")
            print(f"  message={row['message']}")

    section_title("RECENT EXECUTION ORDERS")
    if not recent_orders:
        print("No execution orders stored in SQLite yet.")
    else:
        for row in recent_orders:
            marker = "*" if int(row["id"]) == int(selected_execution_order_id or -1) else " "
            print(
                f"{marker} id={row['id']} | {row['created_at']} | {row['symbol']} | "
                f"{row['side']} | {row['state']}"
            )

    section_title("RECENT EXECUTION EVENTS")
    if not recent_events:
        print("No execution order events stored in SQLite yet.")
    else:
        for row in recent_events:
            print(
                f"id={row['execution_order_id']} | {row['created_at']} | "
                f"{row['event_type']} | {row['from_state']}->{row['to_state']} | {row['message']}"
            )

    section_title("LIVE RECONCILIATION")
    printed_any = False
    for title, rows in (
        ("warnings", live_reconciliation.get("warnings", [])),
        ("partial_fills", live_reconciliation.get("partially_filled_orders", [])),
        ("reserved_without_open_order", live_reconciliation.get("reserved_without_open_order", [])),
        ("open_order_without_reserved", live_reconciliation.get("open_order_without_reserved", [])),
        ("triggered_exit_candidates", live_reconciliation.get("triggered_exit_candidates", [])),
        ("unmanaged_live_holdings", live_reconciliation.get("unmanaged_live_holdings", [])),
    ):
        if not rows:
            continue
        printed_any = True
        print(f"[{title}]")
        for row in rows:
            print(f"- {row}")
    if not printed_any:
        print("No live reconciliation issues detected.")

    print(divider("=") + "\n")


def print_daily_stats_snapshot(today: str, today_stats: dict):
    print()
    print(divider("="))
    section_title(f"DAILY STATS ({today})")

    if not today_stats:
        print("No closed trades today.")
    else:
        print(
            f"{'SYMBOL':<{SYMBOL_COL}} {'TRADES':>8} {'WINS':>8} {'LOSSES':>8} "
            f"{'REALIZED P/L':>16}"
        )
        print(divider("-"))

        total_trades = 0
        total_wins = 0
        total_losses = 0
        total_pnl = 0.0

        for symbol in sorted(today_stats):
            stats = today_stats[symbol]
            total_trades += stats["trades"]
            total_wins += stats["wins"]
            total_losses += stats["losses"]
            total_pnl += stats["realized_pnl_thb"]

            print(
                f"{symbol:<{SYMBOL_COL}} {stats['trades']:8d} {stats['wins']:8d} "
                f"{stats['losses']:8d} {stats['realized_pnl_thb']:16,.2f}"
            )

        print(divider("-"))
        print(
            f"{'TOTAL':<{SYMBOL_COL}} {total_trades:8d} {total_wins:8d} "
            f"{total_losses:8d} {total_pnl:16,.2f}"
        )

    print(divider("=") + "\n")


def _unwrap_result(payload):
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def print_account_snapshot(snapshot: dict):
    print()
    print(divider("="))
    section_title("ACCOUNT SNAPSHOT")

    server_time_entry = snapshot.get("server_time", {})
    wallet_entry = snapshot.get("wallet", {})
    balances_entry = snapshot.get("balances", {})
    open_orders = snapshot.get("open_orders", {})

    server_time = _unwrap_result(server_time_entry)
    wallet = _unwrap_result(wallet_entry)
    balances = _unwrap_result(balances_entry)

    print(f"Server time: {server_time if server_time_entry.get('ok', False) else server_time_entry.get('error')}")
    print()

    section_title("WALLET")
    if not wallet_entry.get("ok", False):
        print(wallet_entry.get("error"))
    elif not wallet:
        print("No wallet data returned.")
    elif isinstance(wallet, dict):
        for key in sorted(wallet):
            print(f"{key}: {wallet[key]}")
    else:
        print(wallet)

    section_title("BALANCES")
    if not balances_entry.get("ok", False):
        print(balances_entry.get("error"))
    elif not balances:
        print("No balances data returned.")
    elif isinstance(balances, dict):
        for asset in sorted(balances):
            print(f"{asset}: {balances[asset]}")
    else:
        print(balances)

    section_title("OPEN ORDERS")
    if not open_orders:
        print("No open orders.")
    elif isinstance(open_orders, dict):
        for symbol in sorted(open_orders):
            entry = open_orders[symbol]
            print(f"[{symbol}]")
            if not entry.get("ok", False):
                print(f"  {entry.get('error')}")
                continue

            payload = _unwrap_result(entry)
            if not payload:
                print("  No open orders.")
            elif isinstance(payload, list):
                for item in payload:
                    print(f"  {item}")
            else:
                print(f"  {payload}")
    else:
        print(open_orders)

    print(divider("=") + "\n")


def print_database_summary(summary: dict, today: str):
    print()
    print(divider("="))
    section_title("DATABASE SUMMARY")

    signals = summary.get("signals", {})
    market_snapshots = summary.get("market_snapshots", {})
    paper_trades = summary.get("paper_trades", {})
    runtime_events = summary.get("runtime_events", [])
    latest_account_snapshot = summary.get("latest_account_snapshot")
    latest_reconciliation = summary.get("latest_reconciliation")

    print(
        f"Signals: today={signals.get('today', 0)} total={signals.get('total', 0)} | "
        f"Paper trades: today={paper_trades.get('today', 0)} total={paper_trades.get('total', 0)}"
    )
    print(
        f"Market snapshots: today={market_snapshots.get('today', 0)} "
        f"total={market_snapshots.get('total', 0)}"
    )
    print(
        f"Realized PnL: today={paper_trades.get('today_realized_pnl', 0.0):,.2f} THB | "
        f"total={paper_trades.get('total_realized_pnl', 0.0):,.2f} THB"
    )
    print(f"Query date: {today}")

    section_title("LATEST ACCOUNT SNAPSHOT")
    if not latest_account_snapshot:
        print("No account snapshots stored in SQLite yet.")
    else:
        print(
            f"time={latest_account_snapshot['created_at']} "
            f"source={latest_account_snapshot['source']} "
            f"status={latest_account_snapshot['private_api_status']}"
        )
        capabilities = latest_account_snapshot.get("capabilities", [])
        if capabilities:
            print(f"capabilities: {' | '.join(capabilities)}")

    section_title("LATEST RECONCILIATION")
    if not latest_reconciliation:
        print("No reconciliation results stored in SQLite yet.")
    else:
        print(
            f"time={latest_reconciliation['created_at']} "
            f"phase={latest_reconciliation['phase']} "
            f"status={latest_reconciliation['status']} "
            f"positions={latest_reconciliation['positions_count']}"
        )
        warnings = latest_reconciliation.get("warnings", [])
        if warnings:
            for warning in warnings:
                print(f"- {warning}")

    section_title("RECENT SIGNALS")
    recent_signals = signals.get("recent", [])
    if not recent_signals:
        print("No signal logs stored in SQLite yet.")
    else:
        for row in recent_signals:
            print(
                f"{row['created_at']} | {row['symbol']} | "
                f"{row['zone']} | {row['status']} | last={row['last_price']:,.8f}"
            )

    section_title("TODAY MARKET ANALYTICS")
    market_analytics = market_snapshots.get("analytics", [])
    if not market_analytics:
        print("No market analytics available yet for today.")
    else:
        for row in market_analytics:
            print(
                f"{row['symbol']} | snapshots={row['snapshots']} | "
                f"first={row['first_price']:,.8f} | last={row['latest_price']:,.8f} | "
                f"min={row['min_price']:,.8f} | max={row['max_price']:,.8f} | "
                f"move={row['day_move_percent']:.2f}% | zone={row['latest_zone']} | "
                f"mode={row['latest_mode']}"
            )

    section_title("RECENT MARKET SNAPSHOTS")
    recent_market_snapshots = market_snapshots.get("recent", [])
    if not recent_market_snapshots:
        print("No market snapshots stored in SQLite yet.")
    else:
        for row in recent_market_snapshots:
            print(
                f"{row['created_at']} | {row['symbol']} | {row['zone']} | "
                f"{row['status']} | last={row['last_price']:,.8f} | mode={row['trading_mode']}"
            )

    section_title("RECENT PAPER TRADES")
    recent_trades = paper_trades.get("recent", [])
    if not recent_trades:
        print("No paper trades stored in SQLite yet.")
    else:
        for row in recent_trades:
            print(
                f"{row['sell_time']} | {row['symbol']} | {row['exit_reason']} | "
                f"pnl={row['pnl_thb']:,.2f} THB ({row['pnl_percent']:.2f}%)"
            )

    section_title("RECENT RUNTIME EVENTS")
    if not runtime_events:
        print("No runtime events stored in SQLite yet.")
    else:
        for row in runtime_events:
            print(
                f"{row['created_at']} | {row['severity'].upper()} | "
                f"{row['event_type']} | {row['message']}"
            )

    print(divider("=") + "\n")


def print_health_snapshot(health: dict):
    print()
    print(divider("="))
    section_title("HEALTH DIAGNOSTICS")

    print(
        f"state={health['runtime_state']} | trading_mode={health['trading_mode']} | "
        f"execution_enabled={'YES' if health['execution_enabled'] else 'NO'}"
    )
    print(
        f"rules={health['rules_count']} | open_positions={health['open_positions']} | "
        f"cooldowns={health['cooldowns']} | tracked_days={health['tracked_days']}"
    )
    print(f"report_filter={health.get('report_filter_symbol') or 'ALL'}")

    section_title("PATHS")
    print(f"config: {health['config_path']}")
    print(f"runtime_state: {health['state_path']}")
    print(f"sqlite: {health['db_path']}")

    section_title("DB STORAGE")
    db_storage = health.get("db_storage", {})
    print(
        f"exists={'YES' if db_storage.get('db_exists') else 'NO'} | "
        f"size={format_bytes(int(db_storage.get('db_size_bytes', 0)))}"
    )
    table_counts = db_storage.get("table_counts", {})
    if table_counts:
        print(
            " | ".join(
                f"{table}={count}" for table, count in sorted(table_counts.items())
            )
        )
    latest_cleanup = db_storage.get("latest_cleanup")
    if latest_cleanup:
        print(f"latest_cleanup: {latest_cleanup['created_at']} | {latest_cleanup['message']}")

    section_title("RETENTION")
    retention_days = health.get("retention_days", {})
    if retention_days:
        for key, value in retention_days.items():
            print(f"{key}: {value} days")

    section_title("PRIVATE API")
    print(f"status: {health['private_api_status']}")
    capabilities = health.get("private_api_capabilities", [])
    if capabilities:
        print(f"capabilities: {' | '.join(capabilities)}")
    open_orders_errors = health.get("open_orders_errors", {})
    if open_orders_errors:
        print("open_orders_errors:")
        for symbol, error in open_orders_errors.items():
            print(f"- {symbol}: {error}")

    section_title("ORDER FOUNDATION")
    order_foundation = health.get("order_foundation", {})
    print(f"status: {order_foundation.get('status', 'unknown')}")
    order_capabilities = order_foundation.get("capabilities", [])
    if order_capabilities:
        print(f"capabilities: {' | '.join(order_capabilities)}")

    section_title("LIVE EXECUTION")
    live_guardrails = health.get("live_execution_guardrails", {})
    if not live_guardrails:
        print("No live execution guardrail snapshot recorded.")
    else:
        print(
            f"ready={'YES' if live_guardrails.get('ready') else 'NO'} | "
            f"kill_switch={'ON' if live_guardrails.get('live_execution_enabled') else 'OFF'} | "
            f"auto_exit={'ON' if live_guardrails.get('live_auto_exit_enabled') else 'OFF'} | "
            f"strategy_wired={'YES' if live_guardrails.get('strategy_execution_wired') else 'NO'}"
        )
        print(
            f"open_orders_capability={live_guardrails.get('open_orders_capability', 'UNKNOWN')} | "
            f"thb_available={float(live_guardrails.get('thb_available_balance', 0.0)):,.2f} | "
            f"min_thb_balance={float(live_guardrails.get('live_min_thb_balance', 0.0)):,.2f}"
        )
        print(
            f"max_order_thb={float(live_guardrails.get('live_max_order_thb', 0.0)):,.2f} | "
            f"slippage_tolerance={float(live_guardrails.get('live_slippage_tolerance_percent', 0.0)):.2f}% | "
            f"daily_loss_limit={float(live_guardrails.get('live_daily_loss_limit_thb', 0.0)):,.2f} THB"
        )
        blocked_reasons = live_guardrails.get("blocked_reasons", [])
        if blocked_reasons:
            print("blocked_reasons:")
            for reason in blocked_reasons:
                print(f"- {reason}")

    section_title("LATEST ACCOUNT SNAPSHOT")
    latest_account_snapshot = health.get("latest_account_snapshot")
    if not latest_account_snapshot:
        print("No account snapshot recorded.")
    else:
        print(
            f"time={latest_account_snapshot['created_at']} "
            f"source={latest_account_snapshot['source']} "
            f"status={latest_account_snapshot['private_api_status']}"
        )
        if latest_account_snapshot.get("capabilities"):
            print(
                f"capabilities: {' | '.join(latest_account_snapshot['capabilities'])}"
            )

    section_title("LATEST RECONCILIATION")
    latest_reconciliation = health.get("latest_reconciliation")
    if not latest_reconciliation:
        print("No reconciliation result recorded.")
    else:
        print(
            f"time={latest_reconciliation['created_at']} "
            f"phase={latest_reconciliation['phase']} "
            f"status={latest_reconciliation['status']} "
            f"positions={latest_reconciliation['positions_count']}"
        )
        warnings = latest_reconciliation.get("warnings", [])
        if warnings:
            for warning in warnings:
                print(f"- {warning}")

    section_title("LIVE RECONCILIATION")
    live_reconciliation = health.get("live_reconciliation", {})
    printed_any = False
    for title, rows in (
        ("warnings", live_reconciliation.get("warnings", [])),
        ("partial_fills", live_reconciliation.get("partially_filled_orders", [])),
        ("reserved_without_open_order", live_reconciliation.get("reserved_without_open_order", [])),
        ("open_order_without_reserved", live_reconciliation.get("open_order_without_reserved", [])),
        ("triggered_exit_candidates", live_reconciliation.get("triggered_exit_candidates", [])),
        ("unmanaged_live_holdings", live_reconciliation.get("unmanaged_live_holdings", [])),
    ):
        if not rows:
            continue
        printed_any = True
        print(f"[{title}]")
        for row in rows:
            print(f"- {row}")
    if not printed_any:
        print("No live reconciliation issues detected.")

    section_title("LATEST EXECUTION ORDER")
    latest_execution_order = health.get("latest_execution_order")
    if not latest_execution_order:
        print("No execution order recorded yet.")
    else:
        print(
            f"id={latest_execution_order['id']} | symbol={latest_execution_order['symbol']} | "
            f"side={latest_execution_order['side']} | type={latest_execution_order['order_type']} | "
            f"state={latest_execution_order['state']}"
        )
        print(f"created={latest_execution_order['created_at']}")
        print(f"updated={latest_execution_order['updated_at']}")
        print(f"message={latest_execution_order['message']}")
        if latest_execution_order.get("exchange_order_id"):
            print(f"exchange_order_id={latest_execution_order['exchange_order_id']}")

    section_title("LAST NOTICE")
    if health.get("notice"):
        print(health["notice"])
        for line in health.get("notice_lines", []) or []:
            print(f"- {line}")
    else:
        print("No active notice.")

    print(divider("=") + "\n")


def print_order_probe(probe: dict):
    print()
    print(divider("="))
    section_title("ORDER FOUNDATION PROBE")

    print(f"status: {probe.get('status', 'unknown')}")
    print(
        f"foundation_ready={'YES' if probe.get('foundation_ready') else 'NO'} | "
        f"execution_locked={'YES' if probe.get('execution_locked') else 'NO'}"
    )
    capabilities = probe.get("capabilities", [])
    if capabilities:
        print(f"capabilities: {' | '.join(capabilities)}")

    section_title("OPEN ORDERS PROBE")
    open_orders = probe.get("open_orders", {})
    if not open_orders:
        print("No open-order probe data.")
    else:
        for symbol in sorted(open_orders):
            entry = open_orders[symbol]
            if entry.get("ok", False):
                print(f"{symbol}: OK")
            else:
                print(f"{symbol}: {entry.get('error')}")

    section_title("ORDER HISTORY PROBE")
    order_history = probe.get("order_history", {})
    if not order_history:
        print("No order-history probe data.")
    else:
        for symbol in sorted(order_history):
            entry = order_history[symbol]
            if entry.get("ok", False):
                print(f"{symbol}: OK")
            else:
                print(f"{symbol}: {entry.get('error')}")

    section_title("ENDPOINT VARIANTS")
    endpoint_variants = probe.get("endpoint_variants", {})
    if not endpoint_variants:
        print("No endpoint variant diagnostics.")
    else:
        for symbol in sorted(endpoint_variants):
            print(f"[{symbol}]")
            for family, variants in endpoint_variants[symbol].items():
                print(f"  {family}:")
                for variant_name, entry in variants.items():
                    if entry.get("ok", False):
                        print(f"    {variant_name}: OK")
                    else:
                        print(f"    {variant_name}: {entry.get('error')}")

    section_title("PAYLOAD EXAMPLES")
    payload_examples = probe.get("payload_examples", {})
    if not payload_examples:
        print("No payload examples generated.")
    else:
        for symbol in sorted(payload_examples):
            print(f"[{symbol}]")
            for key, payload in payload_examples[symbol].items():
                print(f"  {key}: {payload}")

    print(divider("=") + "\n")


def print_reporting_summary(report: dict, today: str):
    print()
    print(divider("="))
    filter_symbol = report.get("filter_symbol")
    filter_label = filter_symbol or "ALL"
    section_title(f"DB REPORTS ({today})")
    print(f"Filter: {filter_label}")

    symbol_summary = report.get("symbol_summary", [])
    recent_trades = report.get("recent_trades", [])
    recent_errors = report.get("recent_errors", [])
    recent_execution_orders = report.get("recent_execution_orders", [])
    recent_auto_exit_events = report.get("recent_auto_exit_events", [])

    section_title("SYMBOL SUMMARY")
    if not symbol_summary:
        print("No symbol report data available yet for today.")
    else:
        for row in symbol_summary:
            print(
                f"{row['symbol']} | zone={row['latest_zone']} | "
                f"snapshots={row['snapshots']} | signals={row['signals']} | "
                f"trades={row['trades']} | wins={row['wins']} | losses={row['losses']} | "
                f"pnl={row['pnl_thb']:,.2f} THB"
            )
            if row["snapshots"] > 0:
                print(
                    f"  first={row['first_price']:,.8f} | last={row['latest_price']:,.8f} | "
                    f"min={row['min_price']:,.8f} | max={row['max_price']:,.8f} | "
                    f"move={row['day_move_percent']:.2f}%"
                )

    section_title("RECENT PAPER TRADE HISTORY")
    if not recent_trades:
        print("No paper trades stored in SQLite yet.")
    else:
        for row in recent_trades:
            print(
                f"{row['sell_time']} | {row['symbol']} | {row['exit_reason']} | "
                f"pnl={row['pnl_thb']:,.2f} THB ({row['pnl_percent']:.2f}%)"
            )

    section_title("RECENT EXECUTION ORDERS")
    if not recent_execution_orders:
        print("No execution orders stored in SQLite yet.")
    else:
        for row in recent_execution_orders:
            print(
                f"id={row['id']} | {row['created_at']} | {row['symbol']} | "
                f"{row['side']} | {row['state']} | {row['message']}"
            )

    section_title("RECENT AUTO EXIT EVENTS")
    if not recent_auto_exit_events:
        print("No auto live exit events stored in SQLite yet.")
    else:
        for row in recent_auto_exit_events:
            print(f"{row['created_at']} | {row['severity'].upper()} | {row['message']}")

    section_title("RECENT RUNTIME ERRORS")
    if not recent_errors:
        print("No runtime errors stored in SQLite yet.")
    else:
        for row in recent_errors:
            print(f"{row['created_at']} | {row['event_type']} | {row['message']}")

    print(divider("=") + "\n")
