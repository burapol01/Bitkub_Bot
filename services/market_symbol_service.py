from __future__ import annotations

from typing import Any

from clients.bitkub_client import get_market_symbols_v3


def normalize_market_symbol(raw_symbol: Any) -> str | None:
    value = str(raw_symbol or "").strip().upper().replace("-", "_")
    if not value:
        return None
    parts = value.split("_")
    if len(parts) != 2:
        return value
    left, right = parts
    if left == "THB":
        return f"THB_{right}"
    if right == "THB":
        return f"THB_{left}"
    return value


def build_market_symbol_directory(payload: list[dict[str, Any]] | list[Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    symbols: list[str] = []
    seen: set[str] = set()
    source_by_symbol: dict[str, str] = {}

    for raw_row in payload:
        if isinstance(raw_row, dict):
            raw_symbol = raw_row.get("symbol") or raw_row.get("id") or raw_row.get("name")
            source = str(raw_row.get("source") or "").strip().lower()
            market_segment = str(raw_row.get("market_segment") or "").strip().upper()
            status = str(raw_row.get("status") or "").strip().lower()
        else:
            raw_symbol = raw_row
            source = ""
            market_segment = ""
            status = ""

        normalized_symbol = normalize_market_symbol(raw_symbol)
        if not normalized_symbol or normalized_symbol in seen:
            continue

        seen.add(normalized_symbol)
        symbols.append(normalized_symbol)
        if source:
            source_by_symbol[normalized_symbol] = source

        rows.append(
            {
                "symbol": normalized_symbol,
                "exchange_symbol": str(raw_symbol or "").strip().upper(),
                "source": source or "unknown",
                "market_segment": market_segment or "n/a",
                "status": status or "unknown",
                "name": str(raw_row.get("name") or "") if isinstance(raw_row, dict) else "",
                "description": str(raw_row.get("description") or "") if isinstance(raw_row, dict) else "",
            }
        )

    rows.sort(key=lambda row: str(row.get("symbol") or ""))
    symbols.sort()
    exchange_symbols = [
        str(row["symbol"])
        for row in rows
        if str(row.get("source") or "").lower() == "exchange"
    ]
    non_exchange_rows = [
        row for row in rows if str(row.get("source") or "").lower() not in {"", "exchange", "unknown"}
    ]

    return {
        "symbols": symbols,
        "rows": rows,
        "source_by_symbol": source_by_symbol,
        "exchange_symbols": exchange_symbols,
        "non_exchange_symbols": [str(row["symbol"]) for row in non_exchange_rows],
        "non_exchange_rows": non_exchange_rows,
        "error": None,
    }


def fetch_market_symbol_directory() -> dict[str, Any]:
    payload = get_market_symbols_v3()
    return build_market_symbol_directory(payload)


def build_non_exchange_symbol_source_map(
    symbols: list[str] | tuple[str, ...] | set[str],
    *,
    source_by_symbol: dict[str, str] | None,
) -> dict[str, str]:
    normalized_source_map = {
        str(symbol).strip().upper(): str(source).strip().lower()
        for symbol, source in (source_by_symbol or {}).items()
        if str(symbol).strip() and str(source).strip()
    }
    blocked: dict[str, str] = {}
    for raw_symbol in symbols:
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            continue
        source = normalized_source_map.get(symbol, "")
        if source and source != "exchange":
            blocked[symbol] = source
    return blocked
