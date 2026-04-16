import csv
import os
from config import load_config
from services.db_service import insert_paper_trade_log, insert_signal_log
from utils.time_utils import coerce_time_text


def get_log_files():
    config = load_config()
    signal_log_file = os.getenv("BITKUB_SIGNAL_LOG_FILE") or config["signal_log_file"]
    trade_log_file = os.getenv("BITKUB_TRADE_LOG_FILE") or config["trade_log_file"]
    return signal_log_file, trade_log_file


def ensure_signal_log_file():
    signal_log_file, _ = get_log_files()

    if not os.path.exists(signal_log_file):
        with open(signal_log_file, mode="w", newline="", encoding="utf-8") as f:
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


def ensure_trade_log_file():
    _, trade_log_file = get_log_files()

    if not os.path.exists(trade_log_file):
        with open(trade_log_file, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
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
            ])


def write_signal_log(timestamp, symbol, last_price, buy_below, sell_above, zone, status):
    signal_log_file, _ = get_log_files()
    normalized_timestamp = coerce_time_text(timestamp)

    with open(signal_log_file, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            normalized_timestamp,
            symbol,
            last_price,
            buy_below,
            sell_above,
            zone,
            status,
        ])

    insert_signal_log(
        created_at=normalized_timestamp,
        symbol=str(symbol),
        last_price=float(last_price),
        buy_below=float(buy_below),
        sell_above=float(sell_above),
        zone=str(zone),
        status=str(status),
    )


def write_trade_log(
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
):
    _, trade_log_file = get_log_files()
    normalized_buy_time = coerce_time_text(buy_time)
    normalized_sell_time = coerce_time_text(sell_time)

    with open(trade_log_file, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            normalized_buy_time,
            normalized_sell_time,
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
        ])

    insert_paper_trade_log(
        buy_time=normalized_buy_time,
        sell_time=normalized_sell_time,
        symbol=str(symbol),
        exit_reason=str(exit_reason),
        budget_thb=float(budget_thb),
        buy_fee_thb=float(buy_fee_thb),
        net_budget_thb=float(net_budget_thb),
        buy_price=float(buy_price),
        sell_price=float(sell_price),
        coin_qty=float(coin_qty),
        gross_proceeds_thb=float(gross_proceeds_thb),
        sell_fee_thb=float(sell_fee_thb),
        net_proceeds_thb=float(net_proceeds_thb),
        pnl_thb=float(pnl_thb),
        pnl_percent=float(pnl_percent),
    )
