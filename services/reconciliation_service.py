from typing import Any

from clients.bitkub_private_client import BitkubPrivateClient, BitkubPrivateClientError


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


def extract_open_orders_by_symbol(account_snapshot: dict | None) -> dict[str, list[dict[str, Any]]]:
    if not account_snapshot:
        return {}

    open_orders_entry = account_snapshot.get("open_orders", {})
    if not isinstance(open_orders_entry, dict):
        return {}

    open_orders_by_symbol: dict[str, list[dict[str, Any]]] = {}

    for symbol, entry in open_orders_entry.items():
        if not isinstance(entry, dict) or not entry.get("ok", False):
            continue
        payload = _unwrap_result(entry)
        if isinstance(payload, list):
            open_orders_by_symbol[symbol] = [
                item for item in payload if isinstance(item, dict)
            ]

    return open_orders_by_symbol


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


def reconcile_execution_orders_with_exchange(
    execution_orders: list[dict[str, Any]],
    account_snapshot: dict | None,
    private_client: BitkubPrivateClient | None = None,
) -> list[str]:
    if not execution_orders:
        return []

    open_orders_by_symbol = extract_open_orders_by_symbol(account_snapshot)
    warnings: list[str] = []

    for order in execution_orders:
        symbol = str(order.get("symbol", ""))
        state = str(order.get("state", ""))
        exchange_order_id = order.get("exchange_order_id")
        side = order.get("side")

        if not symbol:
            warnings.append("execution order has no symbol")
            continue

        if not exchange_order_id:
            warnings.append(
                f"{symbol}: execution order in state={state} has no exchange_order_id recorded"
            )
            continue

        symbol_open_orders = open_orders_by_symbol.get(symbol, [])
        matching_open_order = next(
            (
                item
                for item in symbol_open_orders
                if str(item.get("id")) == str(exchange_order_id)
            ),
            None,
        )
        if matching_open_order is not None:
            continue

        if private_client is None:
            warnings.append(
                f"{symbol}: exchange order {exchange_order_id} is not present in open_orders and order_info lookup is unavailable"
            )
            continue

        try:
            private_client.get_order_info(
                order_id=exchange_order_id,
                symbol=symbol,
                side=str(side) if side else None,
            )
        except BitkubPrivateClientError as e:
            warnings.append(
                f"{symbol}: unable to confirm exchange order {exchange_order_id} via order_info: {e}"
            )

    return warnings


def summarize_live_reconciliation(
    *,
    execution_orders: list[dict[str, Any]],
    live_holdings_rows: list[dict[str, Any]],
    account_snapshot: dict | None,
    private_client: BitkubPrivateClient | None = None,
) -> dict[str, Any]:
    warnings = reconcile_execution_orders_with_exchange(
        execution_orders,
        account_snapshot,
        private_client,
    )
    open_orders_by_symbol = extract_open_orders_by_symbol(account_snapshot)

    partially_filled_orders: list[str] = []
    reserved_without_open_order: list[str] = []
    open_order_without_reserved: list[str] = []
    triggered_exit_candidates: list[str] = []
    unmanaged_live_holdings: list[str] = []

    for order in execution_orders:
        symbol = str(order.get("symbol", ""))
        state = str(order.get("state", ""))
        if state == "partially_filled":
            partially_filled_orders.append(
                f"{symbol}: execution order id={order.get('id')} is partially_filled"
            )

    execution_symbols = {
        str(order.get("symbol", "")) for order in execution_orders if order.get("symbol")
    }

    for row in live_holdings_rows:
        symbol = str(row.get("symbol", ""))
        available_qty = float(row.get("available_qty", 0.0) or 0.0)
        reserved_qty = float(row.get("reserved_qty", 0.0) or 0.0)
        auto_exit_status = str(row.get("auto_exit_status") or "")
        last_execution_side = str(row.get("last_execution_side") or "")
        exchange_open_orders = open_orders_by_symbol.get(symbol, [])

        if reserved_qty > 0 and not exchange_open_orders:
            reserved_without_open_order.append(
                f"{symbol}: reserved balance={reserved_qty:,.8f} but exchange open_orders is empty"
            )

        if exchange_open_orders and reserved_qty <= 0:
            open_order_without_reserved.append(
                f"{symbol}: exchange open_orders exists but reserved balance is 0"
            )

        if auto_exit_status in {
            "STOP_LOSS_TRIGGER",
            "TAKE_PROFIT_TRIGGER",
            "SELL_ZONE_TRIGGER",
        }:
            triggered_exit_candidates.append(f"{symbol}: {auto_exit_status}")

        if (
            symbol not in execution_symbols
            and last_execution_side != "buy"
            and available_qty + reserved_qty > 0
        ):
            unmanaged_live_holdings.append(
                f"{symbol}: live holding exists without a tracked filled buy execution order"
            )

    return {
        "warnings": warnings,
        "partially_filled_orders": partially_filled_orders,
        "reserved_without_open_order": reserved_without_open_order,
        "open_order_without_reserved": open_order_without_reserved,
        "triggered_exit_candidates": triggered_exit_candidates,
        "unmanaged_live_holdings": unmanaged_live_holdings,
    }
