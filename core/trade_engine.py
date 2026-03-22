from config import load_config
from core.strategy import is_in_cooldown, price_change_percent, set_cooldown
from services.alert_service import beep_alert
from services.log_service import write_trade_log
from services.stats_service import can_open_new_trade, record_closed_trade


def get_fee_rate() -> float:
    config = load_config()
    return float(config["fee_rate"])


def open_position(
    symbol: str, last_price: float, config: dict, positions: dict, timestamp: str
):
    fee_rate = get_fee_rate()

    budget_thb = float(config["budget_thb"])
    buy_fee_thb = budget_thb * fee_rate
    net_budget_thb = budget_thb - buy_fee_thb
    coin_qty = net_budget_thb / last_price if last_price > 0 else 0.0
    stop_loss_percent = float(config["stop_loss_percent"])
    take_profit_percent = float(config["take_profit_percent"])
    sell_above = float(config["sell_above"])

    positions[symbol] = {
        "buy_time": timestamp,
        "buy_price": last_price,
        "budget_thb": budget_thb,
        "buy_fee_thb": buy_fee_thb,
        "net_budget_thb": net_budget_thb,
        "coin_qty": coin_qty,
        "fee_rate": fee_rate,
        "stop_loss_percent": stop_loss_percent,
        "take_profit_percent": take_profit_percent,
        "sell_above": sell_above,
    }

    beep_alert("BUY")
    print(
        f"[PAPER BUY]  {symbol} | buy_price={last_price:,.8f} | "
        f"budget={budget_thb:,.2f} THB | fee={buy_fee_thb:,.2f} | "
        f"net={net_budget_thb:,.2f} | qty={coin_qty:,.8f} | "
        f"sell_target={sell_above:,.8f} | sl={stop_loss_percent:.2f}% | "
        f"tp={take_profit_percent:.2f}%"
    )


def close_position(
    symbol: str,
    last_price: float,
    timestamp: str,
    positions: dict,
    daily_stats: dict,
    cooldowns: dict,
    exit_reason: str,
):
    position = positions.get(symbol)
    if position is None:
        return

    fee_rate = float(position.get("fee_rate", get_fee_rate()))
    buy_price = float(position["buy_price"])
    coin_qty = float(position["coin_qty"])
    entry_budget = float(position["budget_thb"])
    buy_fee_thb = float(position["buy_fee_thb"])
    net_budget_thb = float(position["net_budget_thb"])
    buy_time = str(position["buy_time"])

    gross_proceeds_thb = coin_qty * last_price
    sell_fee_thb = gross_proceeds_thb * fee_rate
    net_proceeds_thb = gross_proceeds_thb - sell_fee_thb
    pnl_thb = net_proceeds_thb - entry_budget
    pnl_percent = (pnl_thb / entry_budget) * 100 if entry_budget > 0 else 0.0

    print(
        f"[PAPER SELL] {symbol} | reason={exit_reason} | "
        f"sell_price={last_price:,.8f} | net={net_proceeds_thb:,.2f} | "
        f"P/L={pnl_thb:,.2f} THB ({pnl_percent:.2f}%)"
    )

    write_trade_log(
        buy_time=buy_time,
        sell_time=timestamp,
        symbol=symbol,
        exit_reason=exit_reason,
        budget_thb=entry_budget,
        buy_fee_thb=buy_fee_thb,
        net_budget_thb=net_budget_thb,
        buy_price=buy_price,
        sell_price=last_price,
        coin_qty=coin_qty,
        gross_proceeds_thb=gross_proceeds_thb,
        sell_fee_thb=sell_fee_thb,
        net_proceeds_thb=net_proceeds_thb,
        pnl_thb=pnl_thb,
        pnl_percent=pnl_percent,
    )

    record_closed_trade(daily_stats, symbol, pnl_thb)
    set_cooldown(symbol, cooldowns)
    del positions[symbol]


def handle_symbol(
    symbol: str,
    zone: str,
    zone_changed_flag: bool,
    last_price: float,
    config: dict,
    positions: dict,
    daily_stats: dict,
    cooldowns: dict,
    timestamp: str,
):
    position = positions.get(symbol)
    max_trades_per_day = int(config["max_trades_per_day"])

    if position is None:
        if zone == "BUY" and zone_changed_flag:
            if is_in_cooldown(symbol, cooldowns):
                print(f"[COOLDOWN]   {symbol} | still waiting for cooldown")
                return

            if not can_open_new_trade(daily_stats, symbol, max_trades_per_day):
                print(
                    f"[SKIP BUY]   {symbol} | max_trades_per_day={max_trades_per_day} reached"
                )
                return

            open_position(symbol, last_price, config, positions, timestamp)
        return

    entry_price = float(position["buy_price"])
    stop_loss_percent = float(
        position.get("stop_loss_percent", config["stop_loss_percent"])
    )
    take_profit_percent = float(
        position.get("take_profit_percent", config["take_profit_percent"])
    )
    sell_above = float(position.get("sell_above", config["sell_above"]))
    move_percent = price_change_percent(entry_price, last_price)

    if move_percent <= -stop_loss_percent:
        beep_alert("STOP_LOSS")
        close_position(
            symbol,
            last_price,
            timestamp,
            positions,
            daily_stats,
            cooldowns,
            "STOP_LOSS",
        )
        return

    if move_percent >= take_profit_percent:
        beep_alert("TAKE_PROFIT")
        close_position(
            symbol,
            last_price,
            timestamp,
            positions,
            daily_stats,
            cooldowns,
            "TAKE_PROFIT",
        )
        return

    if last_price >= sell_above:
        beep_alert("SELL")
        close_position(
            symbol,
            last_price,
            timestamp,
            positions,
            daily_stats,
            cooldowns,
            "SELL_ZONE",
        )
