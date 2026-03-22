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
LOG_FILE = "signal_log.csv"


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


def beep_alert(zone: str):
    if not HAS_WINSOUND:
        return

    if zone == "BUY":
        winsound.Beep(1200, 500)
    elif zone == "SELL":
        winsound.Beep(1800, 700)


def ensure_log_file():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "symbol",
                "last_price",
                "buy_below",
                "sell_above",
                "zone",
                "status",
            ])


def write_log(timestamp: str, symbol: str, last_price: float, buy_below: float, sell_above: float, zone: str, status: str):
    with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp,
            symbol,
            last_price,
            buy_below,
            sell_above,
            zone,
            status,
        ])


def check_signals(rules: dict, last_zones: dict):
    ticker = get_ticker()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 110)
    print("BITKUB SIGNAL TRACKER")
    print("เวลา:", timestamp)

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
                beep_alert("BUY")
            elif zone == "SELL":
                status = "SELL SIGNAL: ราคาขึ้นถึงโซนขาย"
                beep_alert("SELL")
            else:
                status = "WAIT: กลับมาอยู่โซนกลาง"

            last_zones[symbol] = zone
            write_log(timestamp, symbol, last_price, buy_below, sell_above, zone, status)
        else:
            if zone == "BUY":
                status = "BUY ZONE"
            elif zone == "SELL":
                status = "SELL ZONE"
            else:
                status = "WAIT ZONE"

        print(
            f"{symbol:10} | last={last_price:14,.8f} | "
            f"buy_below={buy_below:14,.8f} | "
            f"sell_above={sell_above:14,.8f} | "
            f"zone={zone:5} | {status}"
        )


def main():
    ensure_log_file()

    rules = {
        "THB_BTC": {
            "buy_below": 2290000,
            "sell_above": 2330000,
        },
        "THB_ETH": {
            "buy_below": 69500,
            "sell_above": 71000,
        },
        "THB_KUB": {
            "buy_below": 29.30,
            "sell_above": 30.20,
        },
    }

    last_zones = {}
    interval_seconds = 10

    while True:
        try:
            check_signals(rules, last_zones)
        except Exception as e:
            print("เกิดข้อผิดพลาด:", e)

        print(f"\nรอ {interval_seconds} วินาที...\n")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()