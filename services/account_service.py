from typing import Any

from clients.bitkub_private_client import (
    BitkubPrivateClient,
    BitkubPrivateClientError,
    describe_open_orders_symbol_variants,
    is_symbol_required_error_message,
    is_unsupported_symbol_error_message,
)
from config import load_config
from services.reconciliation_service import symbol_to_asset


def _capture_result(fetcher) -> dict[str, Any]:
    try:
        return {"ok": True, "data": fetcher(), "error": None}
    except BitkubPrivateClientError as e:
        return {"ok": False, "data": None, "error": str(e)}


def _is_unsupported_open_orders_error(error: str | None) -> bool:
    return is_unsupported_symbol_error_message(error)


def _is_symbol_required_open_orders_error(error: str | None) -> bool:
    return is_symbol_required_error_message(error)


def _unwrap_result(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def extract_open_order_rows(payload: Any) -> list[Any]:
    rows = _unwrap_result(payload)
    if isinstance(rows, list):
        return rows
    if rows is None:
        return []
    return [rows]


def _classify_open_orders_probe_attempt(entry: dict[str, Any]) -> str:
    if bool(entry.get("ok")):
        return "OK"

    error = str(entry.get("error") or "")
    if _is_unsupported_open_orders_error(error):
        return "UNSUPPORTED"
    if _is_symbol_required_open_orders_error(error):
        return "SYM_REQUIRED"
    return "ERROR"


def _open_orders_request_recipe(*, variant: str, sym_value: str | None) -> str:
    if variant == "without_symbol":
        return "GET my-open-orders"
    if sym_value:
        return f"GET my-open-orders?sym={sym_value}"
    return "GET my-open-orders?sym=?"


def _compact_probe_detail(attempts: list[dict[str, Any]]) -> str:
    labels = {
        "quote_base_lower": "quote_base_lower",
        "base_quote_upper": "base_quote_upper",
        "without_symbol": "without_symbol",
    }
    parts: list[str] = []
    for attempt in attempts:
        variant = str(attempt.get("variant") or "")
        status = str(attempt.get("status") or "")
        parts.append(f"{labels.get(variant, variant)}={status}")
    return " | ".join(parts)


def _summarize_open_orders_probe_attempts(
    *,
    symbol: str,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    symbol_attempts = [
        attempt for attempt in attempts if str(attempt.get("variant")) != "without_symbol"
    ]
    working_symbol_attempt = next(
        (attempt for attempt in symbol_attempts if str(attempt.get("status")) == "OK"),
        None,
    )
    global_attempt = next(
        (attempt for attempt in attempts if str(attempt.get("variant")) == "without_symbol"),
        None,
    )
    global_ok = (
        global_attempt
        if isinstance(global_attempt, dict) and str(global_attempt.get("status")) == "OK"
        else None
    )

    if isinstance(working_symbol_attempt, dict):
        request_recipe = str(working_symbol_attempt.get("request_recipe") or "")
        sym_value = str(working_symbol_attempt.get("sym") or "")
        return {
            "symbol": symbol,
            "status": "SUPPORTED",
            "open_orders": int(working_symbol_attempt.get("open_orders", 0) or 0),
            "working_variant": str(working_symbol_attempt.get("variant") or ""),
            "working_sym": sym_value,
            "request_recipe": request_recipe,
            "next_step": f"Use {request_recipe} for this symbol.",
            "detail": _compact_probe_detail(attempts),
        }

    if isinstance(global_ok, dict):
        return {
            "symbol": symbol,
            "status": "GLOBAL_ONLY",
            "open_orders": int(global_ok.get("open_orders", 0) or 0),
            "working_variant": "without_symbol",
            "working_sym": "",
            "request_recipe": str(global_ok.get("request_recipe") or "GET my-open-orders"),
            "next_step": "Global my-open-orders works. Fetch without sym and filter rows locally for this symbol.",
            "detail": _compact_probe_detail(attempts),
        }

    if symbol_attempts and all(str(attempt.get("status")) == "UNSUPPORTED" for attempt in symbol_attempts):
        return {
            "symbol": symbol,
            "status": "UNSUPPORTED",
            "open_orders": 0,
            "working_variant": "",
            "working_sym": "",
            "request_recipe": "",
            "next_step": "No known sym format passes. Treat this symbol as exchange-side unsupported for my-open-orders.",
            "detail": _compact_probe_detail(attempts),
        }

    return {
        "symbol": symbol,
        "status": "ERROR",
        "open_orders": 0,
        "working_variant": "",
        "working_sym": "",
        "request_recipe": "",
        "next_step": "Inspect the variant details below. This is not the standard endpoint-not-found signature.",
        "detail": _compact_probe_detail(attempts),
    }


def _capture_open_orders_by_symbol(
    client: BitkubPrivateClient,
    *,
    tracked_symbols: list[str],
) -> dict[str, Any]:
    return {
        symbol: _capture_result(lambda symbol=symbol: client.get_open_orders(symbol))
        for symbol in tracked_symbols
    }


def fetch_open_orders_by_symbol_snapshot(
    client: BitkubPrivateClient,
    *,
    symbols: list[str],
) -> dict[str, Any]:
    tracked_symbols = [
        str(symbol).strip()
        for symbol in symbols
        if str(symbol).strip()
    ]
    return _capture_open_orders_by_symbol(
        client,
        tracked_symbols=tracked_symbols,
    )


def probe_open_orders_support_snapshot(
    client: BitkubPrivateClient,
    *,
    symbols: list[str],
    source_by_symbol: dict[str, str] | None = None,
) -> dict[str, Any]:
    tracked_symbols: list[str] = []
    seen: set[str] = set()
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol or symbol in seen:
            continue
        tracked_symbols.append(symbol)
        seen.add(symbol)

    rows: list[dict[str, Any]] = []
    details_by_symbol: dict[str, Any] = {}

    for symbol in tracked_symbols:
        market_source = str((source_by_symbol or {}).get(symbol) or "unknown").strip().lower() or "unknown"
        variant_values = describe_open_orders_symbol_variants(symbol)
        raw_attempts = client.probe_open_orders_variants(symbol)
        attempts: list[dict[str, Any]] = []

        for variant in ("quote_base_lower", "base_quote_upper", "without_symbol"):
            entry = raw_attempts.get(
                variant,
                {"ok": False, "data": None, "error": "variant result missing"},
            )
            payload = entry.get("data")
            open_order_rows = extract_open_order_rows(payload) if bool(entry.get("ok")) else []
            attempt = {
                "variant": variant,
                "sym": variant_values.get(variant),
                "market_source": market_source,
                "status": _classify_open_orders_probe_attempt(entry),
                "ok": bool(entry.get("ok")),
                "open_orders": len(open_order_rows),
                "request_recipe": _open_orders_request_recipe(
                    variant=variant,
                    sym_value=variant_values.get(variant),
                ),
                "error": str(entry.get("error") or ""),
            }
            attempts.append(attempt)

        summary_row = _summarize_open_orders_probe_attempts(
            symbol=symbol,
            attempts=attempts,
        )
        summary_row["market_source"] = market_source
        rows.append(summary_row)
        details_by_symbol[symbol] = {
            "symbol": symbol,
            "market_source": market_source,
            "status": summary_row["status"],
            "request_recipe": summary_row["request_recipe"],
            "next_step": summary_row["next_step"],
            "attempts": attempts,
        }

    rows.sort(
        key=lambda row: (
            {"UNSUPPORTED": 0, "ERROR": 1, "GLOBAL_ONLY": 2, "SUPPORTED": 3}.get(
                str(row.get("status")),
                9,
            ),
            str(row.get("symbol") or ""),
        )
    )

    summary = {
        "symbols": len(rows),
        "supported": sum(1 for row in rows if str(row.get("status")) == "SUPPORTED"),
        "global_only": sum(1 for row in rows if str(row.get("status")) == "GLOBAL_ONLY"),
        "unsupported": sum(1 for row in rows if str(row.get("status")) == "UNSUPPORTED"),
        "error": sum(1 for row in rows if str(row.get("status")) == "ERROR"),
        "open_order_symbols": sum(int(row.get("open_orders", 0) or 0) > 0 for row in rows),
    }

    return {
        "summary": summary,
        "rows": rows,
        "details_by_symbol": details_by_symbol,
        "unsupported_symbols": [
            str(row.get("symbol") or "")
            for row in rows
            if str(row.get("status")) == "UNSUPPORTED"
        ],
        "error_symbols": [
            str(row.get("symbol") or "")
            for row in rows
            if str(row.get("status")) == "ERROR"
        ],
    }


def _normalize_exchange_symbol(
    raw_symbol: Any,
    *,
    tracked_symbols: set[str],
) -> str | None:
    normalized = str(raw_symbol or "").strip().upper()
    if not normalized:
        return None
    if normalized in tracked_symbols:
        return normalized
    if "_" not in normalized:
        return normalized

    left, right = normalized.split("_", 1)
    flipped = f"{right}_{left}"
    if flipped in tracked_symbols:
        return flipped
    return normalized


def _group_global_open_orders(
    payload: Any,
    *,
    tracked_symbols: list[str],
) -> dict[str, Any]:
    tracked_symbol_set = set(tracked_symbols)
    grouped_rows = {symbol: [] for symbol in tracked_symbols}
    raw_rows = _unwrap_result(payload)

    if isinstance(raw_rows, list):
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            symbol = _normalize_exchange_symbol(
                row.get("sym") or row.get("symbol"),
                tracked_symbols=tracked_symbol_set,
            )
            if not symbol:
                continue
            grouped_rows.setdefault(symbol, []).append(row)

    return {
        symbol: {"ok": True, "data": {"result": rows}, "error": None}
        for symbol, rows in grouped_rows.items()
    }


def fetch_account_snapshot(
    client: BitkubPrivateClient,
    *,
    open_orders_mode: str = "per_symbol",
) -> dict[str, Any]:
    config = load_config()
    rules = config.get("rules", {})
    tracked_symbols = sorted(str(symbol) for symbol in rules)

    if open_orders_mode == "none":
        open_orders_by_symbol: dict[str, Any] | None = None
        open_orders_meta: dict[str, Any] = {"mode": "none", "requires_symbol": False}
    elif open_orders_mode == "global":
        global_open_orders = _capture_result(client.get_open_orders)
        if global_open_orders.get("ok", False):
            open_orders_by_symbol = _group_global_open_orders(
                global_open_orders.get("data"),
                tracked_symbols=tracked_symbols,
            )
            open_orders_meta = {"mode": "global", "requires_symbol": False}
        elif _is_symbol_required_open_orders_error(str(global_open_orders.get("error") or "")):
            open_orders_by_symbol = {}
            open_orders_meta = {
                "mode": "global",
                "requires_symbol": True,
                "error": str(global_open_orders.get("error") or ""),
            }
        else:
            open_orders_by_symbol = {"ALL": global_open_orders}
            open_orders_meta = {
                "mode": "global",
                "requires_symbol": False,
                "error": str(global_open_orders.get("error") or ""),
            }
    else:
        open_orders_by_symbol = {}
        open_orders_meta = {"mode": "per_symbol", "requires_symbol": False}
        for symbol in tracked_symbols:
            open_orders_by_symbol[symbol] = _capture_result(
                lambda symbol=symbol: client.get_open_orders(symbol)
            )

    return {
        "server_time": _capture_result(client.get_server_time),
        "wallet": _capture_result(client.get_wallet),
        "balances": _capture_result(client.get_balances),
        "open_orders": open_orders_by_symbol,
        "open_orders_meta": open_orders_meta,
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
                if symbol == "ALL" and _is_symbol_required_open_orders_error(str(entry["error"])):
                    continue
                if _is_unsupported_open_orders_error(str(entry["error"])):
                    continue
                errors.append(f"open_orders[{symbol}]: {entry['error']}")

    return errors


def open_orders_error_map(snapshot: dict[str, Any] | None) -> dict[str, str]:
    if not snapshot:
        return {}

    errors: dict[str, str] = {}
    open_orders = snapshot.get("open_orders", {})
    if not isinstance(open_orders, dict):
        return errors

    for symbol in sorted(open_orders):
        entry = open_orders[symbol]
        if isinstance(entry, dict) and not entry.get("ok", False) and entry.get("error"):
            if _is_unsupported_open_orders_error(str(entry["error"])):
                continue
            errors[symbol] = str(entry["error"])

    return errors


def unsupported_open_orders_symbol_map(snapshot: dict[str, Any] | None) -> dict[str, str]:
    if not snapshot:
        return {}

    unsupported: dict[str, str] = {}
    open_orders = snapshot.get("open_orders", {})
    if not isinstance(open_orders, dict):
        return unsupported

    for symbol in sorted(open_orders):
        entry = open_orders[symbol]
        error = str((entry or {}).get("error") or "")
        if isinstance(entry, dict) and not entry.get("ok", False) and _is_unsupported_open_orders_error(error):
            unsupported[str(symbol)] = error

    return unsupported


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
    open_orders_meta = snapshot.get("open_orders_meta", {})
    open_orders_mode = (
        str(open_orders_meta.get("mode"))
        if isinstance(open_orders_meta, dict)
        else ""
    )
    requires_symbol = bool(
        isinstance(open_orders_meta, dict) and open_orders_meta.get("requires_symbol")
    )

    unsupported_entries = [
        entry
        for entry in open_order_entries
        if isinstance(entry, dict) and _is_unsupported_open_orders_error(str(entry.get("error") or ""))
    ]
    supported_entries = [entry for entry in open_order_entries if entry not in unsupported_entries]

    if requires_symbol:
        open_orders_status = "PARTIAL"
    elif open_orders_mode == "none":
        open_orders_status = "SKIPPED"
    elif not open_order_entries:
        open_orders_status = "UNKNOWN"
    elif supported_entries and all(isinstance(entry, dict) and entry.get("ok", False) for entry in supported_entries):
        open_orders_status = "PARTIAL" if unsupported_entries else "OK"
    elif any(isinstance(entry, dict) and entry.get("ok", False) for entry in open_order_entries):
        open_orders_status = "PARTIAL"
    elif unsupported_entries:
        open_orders_status = "PARTIAL"
    else:
        open_orders_status = "UNAVAILABLE"

    capabilities.append(f"open_orders={open_orders_status}")
    return capabilities


def build_live_holdings_snapshot(
    *,
    account_snapshot: dict | None,
    latest_prices: dict[str, float],
    latest_filled_execution_orders: dict[str, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    config = load_config()
    rules = config.get("rules", {})
    balances = {}

    if account_snapshot:
        balances = account_snapshot.get("balances", {})

    balances_payload = {}
    if isinstance(balances, dict) and balances.get("ok", False):
        balances_payload = balances.get("data", {})
        if isinstance(balances_payload, dict) and "result" in balances_payload:
            balances_payload = balances_payload["result"]

    rows: list[dict[str, Any]] = []
    latest_filled_execution_orders = latest_filled_execution_orders or {}
    asset_rows: list[tuple[str, Any]] = []
    if isinstance(balances_payload, dict):
        asset_rows = sorted(balances_payload.items(), key=lambda item: (item[0] != "THB", str(item[0])))

    for asset, balance_entry in asset_rows:
        if isinstance(balance_entry, dict):
            available_qty = float(balance_entry.get("available", 0.0) or 0.0)
            reserved_qty = float(balance_entry.get("reserved", 0.0) or 0.0)
        else:
            available_qty = float(balance_entry or 0.0)
            reserved_qty = 0.0

        total_qty = available_qty + reserved_qty
        if total_qty <= 0:
            continue

        symbol = "THB" if asset == "THB" else f"THB_{asset}"
        latest_price = 1.0 if asset == "THB" else latest_prices.get(symbol)
        latest_order = latest_filled_execution_orders.get(symbol)
        last_order_rate = None
        last_order_side = None
        if latest_order:
            response_payload = latest_order.get("response_payload", {})
            result = response_payload.get("result", {}) if isinstance(response_payload, dict) else {}
            try:
                last_order_rate = float(result.get("rate"))
            except (TypeError, ValueError):
                last_order_rate = None
            last_order_side = latest_order.get("side")

        market_value_thb = float(latest_price) * total_qty if latest_price is not None else None
        entry_rate = last_order_rate
        stop_loss_price = None
        take_profit_price = None
        sell_above = None
        if asset == "THB":
            auto_exit_status = "CASH"
        elif symbol not in rules:
            auto_exit_status = "UNTRACKED"
        else:
            auto_exit_status = "NO_BUY_REFERENCE"

        if symbol in rules and last_order_side == "buy" and entry_rate is not None:
            rule = rules[symbol]
            stop_loss_price = float(entry_rate) * (
                1 - float(rule["stop_loss_percent"]) / 100
            )
            take_profit_price = float(entry_rate) * (
                1 + float(rule["take_profit_percent"]) / 100
            )
            sell_above = float(rule["sell_above"])

            if reserved_qty > 0 and available_qty <= 0:
                auto_exit_status = "RESERVED_BY_ORDER"
            elif latest_price is None:
                auto_exit_status = "PRICE_UNAVAILABLE"
            elif float(latest_price) <= stop_loss_price:
                auto_exit_status = "STOP_LOSS_TRIGGER"
            elif float(latest_price) >= sell_above:
                auto_exit_status = "SELL_ZONE_TRIGGER"
            elif float(latest_price) >= take_profit_price:
                auto_exit_status = "TAKE_PROFIT_TRIGGER"
            else:
                auto_exit_status = "WAIT"

        rows.append(
            {
                "symbol": symbol,
                "asset": asset,
                "available_qty": available_qty,
                "reserved_qty": reserved_qty,
                "total_qty": total_qty,
                "latest_price": latest_price,
                "market_value_thb": market_value_thb,
                "entry_rate": entry_rate,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "sell_above": sell_above,
                "auto_exit_status": auto_exit_status,
                "last_execution_rate": last_order_rate,
                "last_execution_side": last_order_side,
                "last_execution_order_id": latest_order.get("exchange_order_id") if latest_order else None,
            }
        )

    return rows
