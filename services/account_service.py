from typing import Any

from clients.bitkub_private_client import BitkubPrivateClient, BitkubPrivateClientError
from config import load_config


def _capture_result(fetcher) -> dict[str, Any]:
    try:
        return {"ok": True, "data": fetcher(), "error": None}
    except BitkubPrivateClientError as e:
        return {"ok": False, "data": None, "error": str(e)}


def fetch_account_snapshot(client: BitkubPrivateClient) -> dict[str, Any]:
    config = load_config()
    rules = config.get("rules", {})

    open_orders_by_symbol: dict[str, Any] = {}

    for symbol in sorted(rules):
        open_orders_by_symbol[symbol] = _capture_result(
            lambda symbol=symbol: client.get_open_orders(symbol)
        )

    return {
        "server_time": _capture_result(client.get_server_time),
        "wallet": _capture_result(client.get_wallet),
        "balances": _capture_result(client.get_balances),
        "open_orders": open_orders_by_symbol,
    }


def account_snapshot_errors(snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    for key in ("server_time", "wallet", "balances"):
        entry = snapshot.get(key)
        if isinstance(entry, dict) and not entry.get("ok", False) and entry.get("error"):
            errors.append(f"{key}: {entry['error']}")

    open_orders = snapshot.get("open_orders", {})
    if isinstance(open_orders, dict):
        for symbol in sorted(open_orders):
            entry = open_orders[symbol]
            if isinstance(entry, dict) and not entry.get("ok", False) and entry.get("error"):
                errors.append(f"open_orders[{symbol}]: {entry['error']}")

    return errors


def summarize_account_capabilities(snapshot: dict | None) -> list[str]:
    if not snapshot:
        return ["wallet=unknown", "balances=unknown", "open_orders=unknown"]

    capabilities: list[str] = []

    for key in ("wallet", "balances"):
        entry = snapshot.get(key, {})
        status = "OK" if isinstance(entry, dict) and entry.get("ok", False) else "UNAVAILABLE"
        capabilities.append(f"{key}={status}")

    open_orders = snapshot.get("open_orders", {})
    open_order_entries = (
        list(open_orders.values()) if isinstance(open_orders, dict) else []
    )

    if not open_order_entries:
        open_orders_status = "UNKNOWN"
    elif all(isinstance(entry, dict) and entry.get("ok", False) for entry in open_order_entries):
        open_orders_status = "OK"
    elif any(isinstance(entry, dict) and entry.get("ok", False) for entry in open_order_entries):
        open_orders_status = "PARTIAL"
    else:
        open_orders_status = "UNAVAILABLE"

    capabilities.append(f"open_orders={open_orders_status}")
    return capabilities
