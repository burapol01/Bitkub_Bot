from datetime import timedelta
from config import load_config
from utils.time_utils import now_dt


def get_cooldown_seconds() -> int:
    config = load_config()
    return int(config["cooldown_seconds"])


def get_zone(last_price: float, buy_below: float, sell_above: float) -> str:
    if last_price <= buy_below:
        return "BUY"
    if last_price >= sell_above:
        return "SELL"
    return "WAIT"


def zone_changed(prev_zone: str | None, current_zone: str) -> bool:
    return prev_zone != current_zone


def is_in_cooldown(symbol: str, cooldowns: dict) -> bool:
    if symbol not in cooldowns:
        return False
    return now_dt() < cooldowns[symbol]


def set_cooldown(symbol: str, cooldowns: dict):
    cooldown_seconds = get_cooldown_seconds()
    cooldowns[symbol] = now_dt() + timedelta(seconds=cooldown_seconds)


def price_change_percent(entry_price: float, current_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return ((current_price - entry_price) / entry_price) * 100