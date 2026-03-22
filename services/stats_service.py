from utils.time_utils import today_key


def ensure_daily_stats(daily_stats: dict, symbol: str):
    today = today_key()
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
    today = today_key()
    return daily_stats[today][symbol]["trades"] < max_trades_per_day


def record_closed_trade(daily_stats: dict, symbol: str, pnl_thb: float):
    ensure_daily_stats(daily_stats, symbol)
    today = today_key()

    daily_stats[today][symbol]["trades"] += 1
    daily_stats[today][symbol]["realized_pnl_thb"] += pnl_thb

    if pnl_thb >= 0:
        daily_stats[today][symbol]["wins"] += 1
    else:
        daily_stats[today][symbol]["losses"] += 1


def print_daily_summary(daily_stats: dict):
    today = today_key()
    print("-" * 120)
    print(f"DAILY SUMMARY ({today})")

    if today not in daily_stats or not daily_stats[today]:
        print("No closed trades today.")
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
