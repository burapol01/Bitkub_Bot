import time
import csv
import os
from datetime import datetime
import requests

try:
    import winsound

    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

BASE_URL = "https://api.bitkub.com"
SIGNAL_LOG_FILE = "signal_log.csv"
TRADE_LOG_FILE = "paper_trade_log.csv"

# ค่าธรรมเนียมจำลองต่อฝั่ง 0.25%
FEE_RATE = 0.0025


def get_ticker():
    resp = requests.get(f"{BASE_URL}/api/market/ticker", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_zone(last_price: float, buy_below: float, sell_above: float) -> str:
    if last_price <= buy_below:
        return "BUY"
    if last_price >= sell_above:
        return "SELL"
    return "WAIT"


def beep_alert(kind: str):
    if not HAS_WINSOUND:
        return

    if kind == "BUY":
        winsound.Beep(1200, 500)
    elif kind == "SELL":
        winsound.Beep(1800, 700)
    elif kind == "STOP_LOSS":
        winsound.Beep(900, 900)
    elif kind == "TAKE_PROFIT":
        winsound.Beep(2000, 900)


def ensure_signal_log_file():
    if not os.path.exists(SIGNAL_LOG_FILE):
        with open(SIGNAL_LOG_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "timestamp",
                    "symbol",
                    "last_price",
                    "buy_below",
                    "sell_above",
                    "zone",
                    "status",
                ]
            )


def ensure_trade_log_file():
    if not os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "buy_time",
                    "sell_time",
                    "symbol",
                    "exit_reason",
                    "budget_thb",
                    "buy_fee_thb",
                    "net_budget_thb",
                    "buy_price",
                    "sell_price",
                    "coin_qty",
                    "gross_proceeds_thb",
                    "sell_fee_thb",
                    "net_proceeds_thb",
                    "pnl_thb",
                    "pnl_percent",
                ]
            )


def write_signal_log(
    timestamp: str,
    symbol: str,
    last_price: float,
    buy_below: float,
    sell_above: float,
    zone: str,
    status: str,
):
    with open(SIGNAL_LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                timestamp,
                symbol,
                last_price,
                buy_below,
                sell_above,
                zone,
                status,
            ]
        )


def write_trade_log(
    buy_time: str,
    sell_time: str,
    symbol: str,
    exit_reason: str,
    budget_thb: float,
    buy_fee_thb: float,
    net_budget_thb: float,
    buy_price: float,
    sell_price: float,
    coin_qty: float,
    gross_proceeds_thb: float,
    sell_fee_thb: float,
    net_proceeds_thb: float,
    pnl_thb: float,
    pnl_percent: float,
):
    with open(TRADE_LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                buy_time,
                sell_time,
                symbol,
                exit_reason,
                budget_thb,
                buy_fee_thb,
                net_budget_thb,
                buy_price,
                sell_price,
                coin_qty,
                gross_proceeds_thb,
                sell_fee_thb,
                net_proceeds_thb,
                pnl_thb,
                pnl_percent,
            ]
        )


def get_today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ensure_daily_stats(daily_stats: dict, symbol: str):
    today = get_today_key()
    if today not in daily_stats:
        daily_stats[today] = {}

    if symbol not in daily_stats[today]:
        daily_stats[today][symbol] = {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "realized_pnl_thb": 0.0,
        }


def can_open_new_trade(daily_stats: dict, symbol: str, max_trades_per_day: int) -> bool:
    ensure_daily_stats(daily_stats, symbol)
    today = get_today_key()
    return daily_stats[today][symbol]["trades"] < max_trades_per_day


def close_position(
    symbol: str,
    last_price: float,
    timestamp: str,
    positions: dict,
    daily_stats: dict,
    exit_reason: str,
):
    position = positions.get(symbol)
    if position is None:
        return

    buy_price = float(position["buy_price"])
    coin_qty = float(position["coin_qty"])
    entry_budget = float(position["budget_thb"])
    buy_fee_thb = float(position["buy_fee_thb"])
    net_budget_thb = float(position["net_budget_thb"])
    buy_time = str(position["buy_time"])

    gross_proceeds_thb = coin_qty * last_price
    sell_fee_thb = gross_proceeds_thb * FEE_RATE
    net_proceeds_thb = gross_proceeds_thb - sell_fee_thb

    pnl_thb = net_proceeds_thb - entry_budget
    pnl_percent = (pnl_thb / entry_budget) * 100 if entry_budget > 0 else 0.0

    print(
        f"[PAPER SELL] {symbol} | reason={exit_reason} | sell_price={last_price:,.8f} | "
        f"net={net_proceeds_thb:,.2f} | P/L={pnl_thb:,.2f} THB ({pnl_percent:.2f}%)"
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

    ensure_daily_stats(daily_stats, symbol)
    today = get_today_key()
    daily_stats[today][symbol]["trades"] += 1
    daily_stats[today][symbol]["realized_pnl_thb"] += pnl_thb

    if pnl_thb >= 0:
        daily_stats[today][symbol]["wins"] += 1
    else:
        daily_stats[today][symbol]["losses"] += 1

    del positions[symbol]


def handle_paper_trade(
    symbol: str,
    zone: str,
    last_price: float,
    config: dict,
    positions: dict,
    timestamp: str,
    daily_stats: dict,
):
    position = positions.get(symbol)
    budget_thb = float(config["budget_thb"])
    max_trades_per_day = int(config["max_trades_per_day"])
    stop_loss_percent = float(config["stop_loss_percent"])
    take_profit_percent = float(config["take_profit_percent"])

    # เปิดไม้ใหม่
    if zone == "BUY" and position is None:
        if not can_open_new_trade(daily_stats, symbol, max_trades_per_day):
            print(
                f"[SKIP BUY]   {symbol} | ครบ max_trades_per_day={max_trades_per_day}"
            )
            return

        buy_fee_thb = budget_thb * FEE_RATE
        net_budget_thb = budget_thb - buy_fee_thb
        coin_qty = net_budget_thb / last_price if last_price > 0 else 0.0

        positions[symbol] = {
            "buy_time": timestamp,
            "buy_price": last_price,
            "budget_thb": budget_thb,
            "buy_fee_thb": buy_fee_thb,
            "net_budget_thb": net_budget_thb,
            "coin_qty": coin_qty,
        }

        beep_alert("BUY")
        print(
            f"[PAPER BUY]  {symbol} | buy_price={last_price:,.8f} | "
            f"budget={budget_thb:,.2f} THB | fee={buy_fee_thb:,.2f} | "
            f"net={net_budget_thb:,.2f} | qty={coin_qty:,.8f}"
        )
        return

    # เช็ก exit ถ้ามีสถานะค้าง
    if position is not None:
        buy_price = float(position["buy_price"])
        gross_value = last_price * float(position["coin_qty"])
        estimated_sell_fee = gross_value * FEE_RATE
        estimated_net_value = gross_value - estimated_sell_fee
        current_pnl_percent = (
            (estimated_net_value - float(position["budget_thb"]))
            / float(position["budget_thb"])
        ) * 100

        if current_pnl_percent <= -stop_loss_percent:
            beep_alert("STOP_LOSS")
            close_position(
                symbol, last_price, timestamp, positions, daily_stats, "STOP_LOSS"
            )
            return

        if current_pnl_percent >= take_profit_percent:
            beep_alert("TAKE_PROFIT")
            close_position(
                symbol, last_price, timestamp, positions, daily_stats, "TAKE_PROFIT"
            )
            return

        if zone == "SELL":
            beep_alert("SELL")
            close_position(
                symbol, last_price, timestamp, positions, daily_stats, "SELL_ZONE"
            )
            return


def print_daily_summary(daily_stats: dict):
    today = get_today_key()
    print("-" * 120)
    print(f"DAILY SUMMARY ({today})")

    if today not in daily_stats or not daily_stats[today]:
        print("ยังไม่มีรายการปิดไม้วันนี้")
        return

    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0

    for symbol, stats in daily_stats[today].items():
        total_trades += stats["trades"]
        total_wins += stats["wins"]
        total_losses += stats["losses"]
        total_pnl += stats["realized_pnl_thb"]

        print(
            f"{symbol:10} | trades={stats['trades']:3d} | "
            f"wins={stats['wins']:3d} | losses={stats['losses']:3d} | "
            f"realized P/L={stats['realized_pnl_thb']:10.2f} THB"
        )

    print("-" * 120)
    print(
        f"TOTAL      | trades={total_trades:3d} | "
        f"wins={total_wins:3d} | losses={total_losses:3d} | "
        f"realized P/L={total_pnl:10.2f} THB"
    )


def check_signals(rules: dict, last_zones: dict, positions: dict, daily_stats: dict):
    ticker = get_ticker()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 120)
    print("BITKUB PAPER TRADE TRACKER")
    print("เวลา:", timestamp)
    print(f"Fee rate per side: {FEE_RATE * 100:.2f}%")

    for symbol, config in rules.items():
        if symbol not in ticker:
            print(f"{symbol:10} | ไม่พบข้อมูล")
            continue

        last_price = float(ticker[symbol]["last"])
        buy_below = float(config["buy_below"])
        sell_above = float(config["sell_above"])

        zone = get_zone(last_price, buy_below, sell_above)
        prev_zone = last_zones.get(symbol)

        if zone != prev_zone:
            if zone == "BUY":
                status = "BUY SIGNAL: ราคาลงถึงโซนซื้อ"
            elif zone == "SELL":
                status = "SELL SIGNAL: ราคาขึ้นถึงโซนขาย"
            else:
                status = "WAIT: กลับมาอยู่โซนกลาง"

            last_zones[symbol] = zone
            write_signal_log(
                timestamp, symbol, last_price, buy_below, sell_above, zone, status
            )
        else:
            if zone == "BUY":
                status = "BUY ZONE"
            elif zone == "SELL":
                status = "SELL ZONE"
            else:
                status = "WAIT ZONE"

        handle_paper_trade(
            symbol, zone, last_price, config, positions, timestamp, daily_stats
        )

        if symbol in positions:
            pos = positions[symbol]
            gross_value = last_price * pos["coin_qty"]
            estimated_sell_fee = gross_value * FEE_RATE
            net_value = gross_value - estimated_sell_fee
            unrealized_pnl = net_value - pos["budget_thb"]
            unrealized_pnl_percent = (
                (unrealized_pnl / pos["budget_thb"]) * 100
                if pos["budget_thb"] > 0
                else 0.0
            )

            holding_text = (
                f"HOLDING | entry={pos['buy_price']:,.8f} | "
                f"qty={pos['coin_qty']:,.8f} | est_net={net_value:,.2f} | "
                f"uPnL={unrealized_pnl:,.2f} THB ({unrealized_pnl_percent:.2f}%)"
            )
        else:
            holding_text = "NO POSITION"

        print(
            f"{symbol:10} | last={last_price:14,.8f} | "
            f"buy_below={buy_below:14,.8f} | "
            f"sell_above={sell_above:14,.8f} | "
            f"zone={zone:5} | {status} | {holding_text}"
        )

    print_daily_summary(daily_stats)


def main():
    ensure_signal_log_file()
    ensure_trade_log_file()

    rules = {
        "THB_BTC": {
            "buy_below": 2290000,
            "sell_above": 2330000,
            "budget_thb": 100,
            "stop_loss_percent": 1.5,
            "take_profit_percent": 2.0,
            "max_trades_per_day": 2,
        },
        "THB_ETH": {
            "buy_below": 69500,
            "sell_above": 71000,
            "budget_thb": 100,
            "stop_loss_percent": 1.5,
            "take_profit_percent": 2.0,
            "max_trades_per_day": 2,
        },
        "THB_KUB": {
            "buy_below": 29.75,
            "sell_above": 29.90,
            "budget_thb": 100,
            "stop_loss_percent": 0.4,
            "take_profit_percent": 0.6,
            "max_trades_per_day": 3,
        },
    }

    last_zones = {}
    positions = {}
    daily_stats = {}
    interval_seconds = 10

    while True:
        try:
            check_signals(rules, last_zones, positions, daily_stats)
        except Exception as e:
            print("เกิดข้อผิดพลาด:", e)

        print(f"\nรอ {interval_seconds} วินาที...\n")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()
