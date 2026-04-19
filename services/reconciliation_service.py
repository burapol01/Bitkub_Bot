from typing import Any

from clients.bitkub_private_client import BitkubPrivateClient, BitkubPrivateClientError
from utils.time_utils import now_dt, parse_time_text


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
    findings = collect_runtime_reconciliation_findings(
        execution_orders=execution_orders,
        live_holdings_rows=[],
        account_snapshot=account_snapshot,
    )
    warning_messages = [
        item["message"]
        for item in findings["mismatches"]["orders_without_exchange_id"]
        + findings["mismatches"]["missing_on_exchange"]
    ]
    if warning_messages:
        return warning_messages

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


def _order_age_seconds(order: dict[str, Any], *, reference_dt=None) -> float | None:
    reference = reference_dt or now_dt()
    timestamp_text = str(order.get("updated_at") or order.get("created_at") or "").strip()
    if not timestamp_text:
        return None
    try:
        return max(0.0, (reference - parse_time_text(timestamp_text)).total_seconds())
    except ValueError:
        return None


def collect_runtime_reconciliation_findings(
    *,
    execution_orders: list[dict[str, Any]],
    live_holdings_rows: list[dict[str, Any]],
    account_snapshot: dict | None,
    runtime_state_metadata: dict[str, Any] | None = None,
    stale_order_seconds: int = 1800,
    stale_runtime_state_seconds: int = 86400,
) -> dict[str, Any]:
    open_orders_by_symbol = extract_open_orders_by_symbol(account_snapshot)
    exchange_open_orders_count = sum(
        len(rows) for rows in open_orders_by_symbol.values()
    )
    account_sync_status = "ready" if account_snapshot else "unavailable"
    mismatches: dict[str, list[dict[str, Any]]] = {
        "missing_locally": [],
        "missing_on_exchange": [],
        "orders_without_exchange_id": [],
        "stale_pending": [],
        "partially_filled": [],
        "reserved_without_open_order": [],
        "open_order_without_reserved": [],
        "unmanaged_live_holdings": [],
        "runtime_state_stale": [],
    }

    local_order_ids: set[str] = set()
    exchange_open_order_ids: set[str] = set()

    for order in execution_orders:
        symbol = str(order.get("symbol", ""))
        state = str(order.get("state", ""))
        exchange_order_id = str(order.get("exchange_order_id") or "").strip()
        age_seconds = _order_age_seconds(order)

        if exchange_order_id:
            local_order_ids.add(exchange_order_id)
        else:
            mismatches["orders_without_exchange_id"].append(
                {
                    "symbol": symbol,
                    "state": state,
                    "execution_order_id": int(order.get("id", 0) or 0),
                    "message": (
                        f"{symbol}: execution order in state={state} has no exchange_order_id recorded"
                    ),
                }
            )

        if age_seconds is not None and age_seconds >= float(stale_order_seconds):
            mismatches["stale_pending"].append(
                {
                    "symbol": symbol,
                    "state": state,
                    "execution_order_id": int(order.get("id", 0) or 0),
                    "age_seconds": age_seconds,
                    "message": (
                        f"{symbol}: execution order id={order.get('id')} is stale in state={state} "
                        f"for {int(age_seconds)}s"
                    ),
                }
            )

        if state == "partially_filled":
            mismatches["partially_filled"].append(
                {
                    "symbol": symbol,
                    "execution_order_id": int(order.get("id", 0) or 0),
                    "message": (
                        f"{symbol}: execution order id={order.get('id')} is partially_filled"
                    ),
                }
            )

    for symbol, symbol_open_orders in open_orders_by_symbol.items():
        for item in symbol_open_orders:
            exchange_order_id = str(item.get("id") or "").strip()
            if exchange_order_id:
                exchange_open_order_ids.add(exchange_order_id)
            if exchange_order_id and exchange_order_id not in local_order_ids:
                mismatches["missing_locally"].append(
                    {
                        "symbol": symbol,
                        "exchange_order_id": exchange_order_id,
                        "side": item.get("side"),
                        "message": (
                            f"{symbol}: exchange open order {exchange_order_id} exists without a local execution_order"
                        ),
                    }
                )

    if account_snapshot:
        for order in execution_orders:
            exchange_order_id = str(order.get("exchange_order_id") or "").strip()
            symbol = str(order.get("symbol", ""))
            state = str(order.get("state", ""))
            if exchange_order_id and exchange_order_id not in exchange_open_order_ids:
                mismatches["missing_on_exchange"].append(
                    {
                        "symbol": symbol,
                        "state": state,
                        "execution_order_id": int(order.get("id", 0) or 0),
                        "exchange_order_id": exchange_order_id,
                        "message": (
                            f"{symbol}: local execution order id={order.get('id')} exchange_order_id={exchange_order_id} "
                            "is not visible in the current exchange open-orders snapshot"
                        ),
                    }
                )

    execution_symbols = {
        str(order.get("symbol", "")) for order in execution_orders if order.get("symbol")
    }

    for row in live_holdings_rows:
        symbol = str(row.get("symbol", ""))
        available_qty = float(row.get("available_qty", 0.0) or 0.0)
        reserved_qty = float(row.get("reserved_qty", 0.0) or 0.0)
        last_execution_side = str(row.get("last_execution_side") or "")
        exchange_open_orders = open_orders_by_symbol.get(symbol, [])

        if reserved_qty > 0 and not exchange_open_orders:
            mismatches["reserved_without_open_order"].append(
                {
                    "symbol": symbol,
                    "reserved_qty": reserved_qty,
                    "message": (
                        f"{symbol}: reserved balance={reserved_qty:,.8f} but exchange open_orders is empty"
                    ),
                }
            )

        if exchange_open_orders and reserved_qty <= 0:
            mismatches["open_order_without_reserved"].append(
                {
                    "symbol": symbol,
                    "open_orders": len(exchange_open_orders),
                    "message": (
                        f"{symbol}: exchange open_orders exists but reserved balance is 0"
                    ),
                }
            )

        if (
            symbol not in execution_symbols
            and last_execution_side != "buy"
            and available_qty + reserved_qty > 0
        ):
            mismatches["unmanaged_live_holdings"].append(
                {
                    "symbol": symbol,
                    "available_qty": available_qty,
                    "reserved_qty": reserved_qty,
                    "message": (
                        f"{symbol}: live holding exists without a tracked filled buy execution order"
                    ),
                }
            )

    runtime_state_status = "fresh"
    runtime_state_saved_at = None
    if isinstance(runtime_state_metadata, dict):
        runtime_state_saved_at = runtime_state_metadata.get("saved_at")
        if runtime_state_metadata.get("loaded_from_pending"):
            runtime_state_status = "warning"
            mismatches["runtime_state_stale"].append(
                {
                    "message": "runtime state was restored from runtime_state.pending.json",
                }
            )
        if isinstance(runtime_state_saved_at, str) and runtime_state_saved_at.strip():
            try:
                age_seconds = max(
                    0.0,
                    (now_dt() - parse_time_text(runtime_state_saved_at)).total_seconds(),
                )
                if age_seconds >= float(stale_runtime_state_seconds):
                    runtime_state_status = "warning"
                    mismatches["runtime_state_stale"].append(
                        {
                            "saved_at": runtime_state_saved_at,
                            "age_seconds": age_seconds,
                            "message": (
                                f"runtime state snapshot is stale; saved_at={runtime_state_saved_at}"
                            ),
                        }
                    )
            except ValueError:
                runtime_state_status = "warning"
                mismatches["runtime_state_stale"].append(
                    {
                        "saved_at": runtime_state_saved_at,
                        "message": (
                            f"runtime state saved_at is invalid: {runtime_state_saved_at}"
                        ),
                    }
                )

    mismatch_counts = {
        name: len(items) for name, items in mismatches.items()
    }
    unresolved_count = sum(mismatch_counts.values())
    messages = [
        item["message"]
        for items in mismatches.values()
        for item in items
        if item.get("message")
    ]

    return {
        "account_sync_status": account_sync_status,
        "runtime_state_status": runtime_state_status,
        "runtime_state_saved_at": runtime_state_saved_at,
        "local_open_orders_count": len(execution_orders),
        "exchange_open_orders_count": exchange_open_orders_count,
        "mismatches": mismatches,
        "mismatch_counts": mismatch_counts,
        "unresolved_count": unresolved_count,
        "messages": messages,
    }


def summarize_live_reconciliation(
    *,
    execution_orders: list[dict[str, Any]],
    live_holdings_rows: list[dict[str, Any]],
    account_snapshot: dict | None,
    private_client: BitkubPrivateClient | None = None,
) -> dict[str, Any]:
    findings = collect_runtime_reconciliation_findings(
        execution_orders=execution_orders,
        live_holdings_rows=live_holdings_rows,
        account_snapshot=account_snapshot,
    )
    triggered_exit_candidates: list[str] = []
    for row in live_holdings_rows:
        symbol = str(row.get("symbol", ""))
        auto_exit_status = str(row.get("auto_exit_status") or "")
        if auto_exit_status in {
            "STOP_LOSS_TRIGGER",
            "TAKE_PROFIT_TRIGGER",
            "SELL_ZONE_TRIGGER",
        }:
            triggered_exit_candidates.append(f"{symbol}: {auto_exit_status}")

    return {
        "warnings": [item["message"] for item in findings["mismatches"]["missing_on_exchange"]],
        "missing_locally": [item["message"] for item in findings["mismatches"]["missing_locally"]],
        "partially_filled_orders": [item["message"] for item in findings["mismatches"]["partially_filled"]],
        "reserved_without_open_order": [item["message"] for item in findings["mismatches"]["reserved_without_open_order"]],
        "open_order_without_reserved": [item["message"] for item in findings["mismatches"]["open_order_without_reserved"]],
        "triggered_exit_candidates": triggered_exit_candidates,
        "unmanaged_live_holdings": [item["message"] for item in findings["mismatches"]["unmanaged_live_holdings"]],
        "stale_pending_orders": [item["message"] for item in findings["mismatches"]["stale_pending"]],
        "orders_without_exchange_id": [item["message"] for item in findings["mismatches"]["orders_without_exchange_id"]],
        "mismatch_counts": findings["mismatch_counts"],
        "account_sync_status": findings["account_sync_status"],
        "unresolved_count": findings["unresolved_count"],
    }
