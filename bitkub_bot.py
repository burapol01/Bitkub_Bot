import time
from datetime import datetime
import requests

BASE_URL = "https://api.bitkub.com"


def get_ticker():
    resp = requests.get(f"{BASE_URL}/api/market/ticker", timeout=10)
    resp.raise_for_status()
    return resp.json()


def check_targets(targets: dict[str, float], alerted: dict[str, bool]):
    ticker = get_ticker()

    print("=" * 72)
    print("BITKUB PRICE TRACKER")
    print("เวลา:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    for symbol, target_price in targets.items():
        if symbol not in ticker:
            print(f"{symbol:10} | ไม่พบข้อมูล")
            continue

        last_price = float(ticker[symbol]["last"])
        diff = last_price - target_price
        reached = last_price >= target_price

        if reached and not alerted.get(symbol, False):
            status = "ALERT: เพิ่งถึงเป้าครั้งแรก"
            alerted[symbol] = True
        elif reached and alerted.get(symbol, False):
            status = "ยังอยู่เหนือเป้า"
        else:
            status = "ยังไม่ถึงเป้า"
            alerted[symbol] = False

        print(
            f"{symbol:10} | last={last_price:14,.8f} | "
            f"target={target_price:14,.8f} | diff={diff:14,.8f} | {status}"
        )


def main():
    targets = {
        "THB_BTC": 2300000,
        "THB_ETH": 70000,
        "THB_KUB": 30,
    }

    alerted = {}
    interval_seconds = 10

    while True:
        try:
            check_targets(targets, alerted)
        except Exception as e:
            print("เกิดข้อผิดพลาด:", e)

        print(f"\nรอ {interval_seconds} วินาที...\n")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    main()