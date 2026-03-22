from typing import Any


def _unwrap_result(payload):
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def symbol_to_asset(symbol: str) -> str:
    parts = symbol.split("_", 1)
    if len(parts) != 2:
        return symbol
    quote_asset, base_asset = parts
    return base_asset


def extract_available_balances(account_snapshot: dict | None) -> dict[str, float]:
    if not account_snapshot:
        return {}

    balances_entry = account_snapshot.get("balances", {})
    if not isinstance(balances_entry, dict) or not balances_entry.get("ok", False):
        return {}

    balances_payload = _unwrap_result(balances_entry)
    if not isinstance(balances_payload, dict):
        return {}

    available_balances: dict[str, float] = {}

    for asset, value in balances_payload.items():
        if isinstance(value, dict):
            available = value.get("available", 0)
        else:
            available = value

        try:
            available_balances[asset] = float(available)
        except (TypeError, ValueError):
            continue

    return available_balances


def reconcile_positions_with_balances(
    positions: dict,
    account_snapshot: dict | None,
    *,
    quantity_tolerance: float = 1e-8,
) -> list[str]:
    if not positions:
        return []

    available_balances = extract_available_balances(account_snapshot)
    if not available_balances:
        return ["Unable to reconcile positions because no readable balances are available from the exchange."]

    warnings: list[str] = []

    for symbol in sorted(positions):
        position = positions[symbol]
        asset = symbol_to_asset(symbol)

        try:
            local_qty = float(position.get("coin_qty", 0.0))
        except (TypeError, ValueError):
            warnings.append(f"{symbol}: local position quantity is invalid")
            continue

        exchange_qty = float(available_balances.get(asset, 0.0))
        if exchange_qty + quantity_tolerance < local_qty:
            warnings.append(
                f"{symbol}: local qty={local_qty:.8f} exceeds exchange available {asset}={exchange_qty:.8f}"
            )

    return warnings
