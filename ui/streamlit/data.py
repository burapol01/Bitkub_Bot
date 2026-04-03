from __future__ import annotations

from typing import Any

import streamlit as st

from clients.bitkub_client import get_ticker
from clients.bitkub_private_client import (
    BitkubMissingCredentialsError,
    BitkubPrivateClient,
    BitkubPrivateClientError,
)
from services.account_service import (
    account_snapshot_errors,
    fetch_account_snapshot,
    summarize_account_capabilities,
)
from services.state_service import load_runtime_state
from utils.time_utils import today_key


@st.cache_data(ttl=5, show_spinner=False)
def _cached_runtime_snapshot() -> dict[str, Any]:
    runtime_last_zones: dict[str, Any] = {}
    runtime_positions: dict[str, Any] = {}
    runtime_daily_stats: dict[str, Any] = {}
    runtime_cooldowns: dict[str, Any] = {}
    manual_pause, messages = load_runtime_state(
        runtime_last_zones,
        runtime_positions,
        runtime_daily_stats,
        runtime_cooldowns,
    )
    return {
        "manual_pause": manual_pause,
        "messages": messages,
        "last_zones": runtime_last_zones,
        "positions": runtime_positions,
        "daily_stats": runtime_daily_stats,
        "cooldowns": runtime_cooldowns,
    }


def runtime_snapshot() -> dict[str, Any]:
    return _cached_runtime_snapshot()


def calc_daily_totals(daily_stats: dict[str, Any]) -> tuple[int, int, int, float]:
    today_stats = daily_stats.get(today_key(), {})
    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0

    for stats in today_stats.values():
        total_trades += int(stats["trades"])
        total_wins += int(stats["wins"])
        total_losses += int(stats["losses"])
        total_pnl += float(stats["realized_pnl_thb"])

    return total_trades, total_wins, total_losses, total_pnl


def capability_badge_tone(item: str) -> str:
    capability = str(item or "")
    if capability.endswith("=OK"):
        return "good"
    if capability.endswith("=PARTIAL"):
        return "warn"
    if capability.endswith("=SKIPPED") or capability.endswith("=UNKNOWN"):
        return "info"
    return "bad"


@st.cache_data(ttl=5, show_spinner=False)
def _cached_private_context(
    rule_symbols: tuple[str, ...],
    open_orders_mode: str,
) -> dict[str, Any]:
    private_api_status = "not configured"
    private_api_capabilities: list[str] = ["wallet=OFF", "balances=OFF", "open_orders=OFF"]
    account_snapshot: dict[str, Any] | None = None
    errors: list[str] = []
    _ = rule_symbols

    try:
        candidate = BitkubPrivateClient.from_env()
        if candidate.is_configured():
            account_snapshot = fetch_account_snapshot(
                candidate,
                open_orders_mode=open_orders_mode,
            )
            private_api_capabilities = summarize_account_capabilities(account_snapshot)
            snapshot_errors = account_snapshot_errors(account_snapshot)
            if snapshot_errors:
                private_api_status = "wallet/balance ready, some order endpoints unavailable"
                errors = snapshot_errors
            else:
                open_orders_meta = (
                    account_snapshot.get("open_orders_meta", {})
                    if isinstance(account_snapshot, dict)
                    else {}
                )
                if open_orders_mode == "none":
                    private_api_status = "wallet/balance ready"
                elif isinstance(open_orders_meta, dict) and open_orders_meta.get("requires_symbol"):
                    private_api_status = "wallet/balance ready; open-orders requires per-symbol queries"
                else:
                    private_api_status = "wallet/balance/open-orders ready"
    except BitkubMissingCredentialsError:
        private_api_status = "missing credentials"
    except BitkubPrivateClientError as e:
        private_api_status = "private API error"
        errors = [str(e)]

    return {
        "account_snapshot": account_snapshot,
        "private_api_status": private_api_status,
        "private_api_capabilities": private_api_capabilities,
        "errors": errors,
}


def private_context(
    *,
    rule_symbols: tuple[str, ...] = (),
    open_orders_mode: str = "none",
) -> dict[str, Any]:
    payload = dict(_cached_private_context(rule_symbols, open_orders_mode))
    client: BitkubPrivateClient | None = None

    try:
        candidate = BitkubPrivateClient.from_env()
        if candidate.is_configured():
            client = candidate
    except BitkubMissingCredentialsError:
        client = None

    payload["client"] = client
    return payload


def sidebar_private_context() -> dict[str, Any]:
    client: BitkubPrivateClient | None = None
    private_api_status = "not configured"
    private_api_capabilities = [
        "wallet=UNKNOWN",
        "balances=UNKNOWN",
        "open_orders=UNKNOWN",
    ]

    try:
        candidate = BitkubPrivateClient.from_env()
        if candidate.is_configured():
            client = candidate
            private_api_status = "credentials loaded; network checks run on page load"
        else:
            private_api_status = "not configured"
    except BitkubMissingCredentialsError:
        private_api_status = "missing credentials"

    return {
        "client": client,
        "account_snapshot": None,
        "private_api_status": private_api_status,
        "private_api_capabilities": private_api_capabilities,
        "errors": [],
    }


@st.cache_data(ttl=5, show_spinner=False)
def _cached_ticker() -> dict[str, Any]:
    return get_ticker()


@st.cache_data(ttl=20, show_spinner=False)
def _cached_overview_market_payload(
    rule_signature: tuple[tuple[str, float, float], ...],
) -> dict[str, Any]:
    ticker = get_ticker()
    latest_prices = {
        symbol: float(payload["last"])
        for symbol, payload in ticker.items()
        if isinstance(payload, dict) and "last" in payload
    }
    rows: list[dict[str, Any]] = []

    for symbol, buy_below, sell_above in rule_signature:
        last_price = latest_prices.get(symbol)
        current_zone = "n/a"
        if last_price is not None:
            if last_price <= buy_below:
                current_zone = "BUY"
            elif last_price >= sell_above:
                current_zone = "SELL"
            else:
                current_zone = "WAIT"

        rows.append(
            {
                "symbol": symbol,
                "last_price": last_price,
                "buy_below": buy_below,
                "sell_above": sell_above,
                "zone": current_zone,
            }
        )

    return {
        "latest_prices": latest_prices,
        "ticker_rows": rows,
    }


@st.cache_data(ttl=20, show_spinner=False)
def _cached_overview_private_context(
    rule_symbols: tuple[str, ...],
) -> dict[str, Any]:
    return dict(_cached_private_context(rule_symbols, "none"))


def market_rows(config: dict[str, Any], ticker: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, rule in sorted(config["rules"].items()):
        ticker_entry = ticker.get(symbol, {})
        last_price = (
            float(ticker_entry["last"])
            if isinstance(ticker_entry, dict) and "last" in ticker_entry
            else None
        )
        current_zone = "n/a"
        if last_price is not None:
            buy_below = float(rule["buy_below"])
            sell_above = float(rule["sell_above"])
            if last_price <= buy_below:
                current_zone = "BUY"
            elif last_price >= sell_above:
                current_zone = "SELL"
            else:
                current_zone = "WAIT"

        rows.append(
            {
                "symbol": symbol,
                "last_price": last_price,
                "buy_below": float(rule["buy_below"]),
                "sell_above": float(rule["sell_above"]),
                "zone": current_zone,
            }
        )
    return rows


def build_dashboard_context(config: dict[str, Any]) -> dict[str, Any]:
    runtime = runtime_snapshot()
    rule_symbols = tuple(sorted(str(symbol) for symbol in config.get("rules", {})))
    private_ctx = private_context(
        rule_symbols=rule_symbols,
        open_orders_mode="global",
    )
    ticker = _cached_ticker()
    latest_prices = {
        symbol: float(payload["last"])
        for symbol, payload in ticker.items()
        if isinstance(payload, dict) and "last" in payload
    }

    return {
        "today": today_key(),
        "runtime": runtime,
        "private_ctx": private_ctx,
        "latest_prices": latest_prices,
        "ticker_rows": market_rows(config, ticker),
    }


def build_overview_context(config: dict[str, Any]) -> dict[str, Any]:
    runtime = runtime_snapshot()
    rule_symbols = tuple(sorted(str(symbol) for symbol in config.get("rules", {})))
    private_ctx = _cached_overview_private_context(rule_symbols)
    rule_signature = tuple(
        (
            str(symbol),
            float(rule["buy_below"]),
            float(rule["sell_above"]),
        )
        for symbol, rule in sorted(config.get("rules", {}).items())
    )
    market_payload = _cached_overview_market_payload(rule_signature)

    return {
        "today": today_key(),
        "runtime": runtime,
        "private_ctx": private_ctx,
        "latest_prices": dict(market_payload.get("latest_prices") or {}),
        "ticker_rows": list(market_payload.get("ticker_rows") or []),
    }
