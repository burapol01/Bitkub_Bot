from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import requests

from config import load_config


def _base_url() -> str:
    config = load_config()
    return str(config["base_url"]).rstrip("/")


def get_ticker() -> dict[str, Any]:
    resp = requests.get(f"{_base_url()}/api/market/ticker", timeout=10)
    resp.raise_for_status()
    return resp.json()


def to_tradingview_symbol(symbol: str) -> str:
    parts = str(symbol).split("_")
    if len(parts) != 2:
        return str(symbol).upper()
    quote, base = parts
    return f"{base.upper()}_{quote.upper()}"


def from_tradingview_symbol(symbol: str) -> str:
    parts = str(symbol).split("_")
    if len(parts) != 2:
        return str(symbol).upper()
    base, quote = parts
    return f"{quote.upper()}_{base.upper()}"


def get_market_symbols_v3() -> list[dict[str, Any]]:
    resp = requests.get(f"{_base_url()}/api/v3/market/symbols", timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        return list(payload.get("result", []))
    return []


def get_tradingview_history(
    *,
    symbol: str,
    resolution: str,
    from_ts: int,
    to_ts: int,
) -> dict[str, Any]:
    params = {
        "symbol": to_tradingview_symbol(symbol),
        "resolution": str(resolution),
        "from": int(from_ts),
        "to": int(to_ts),
    }
    resp = requests.get(f"{_base_url()}/tradingview/history", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def build_history_window(*, days: int) -> tuple[int, int]:
    now = datetime.now()
    start = now - timedelta(days=int(days))
    return int(start.timestamp()), int(now.timestamp())
