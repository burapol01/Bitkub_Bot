from __future__ import annotations

from typing import Any

from services.account_service import (
    build_live_holdings_snapshot,
)
from services.db_service import (
    fetch_latest_filled_execution_orders_by_symbol,
    fetch_open_execution_orders,
    fetch_recent_trade_journal,
)
from services.reconciliation_service import (
    collect_runtime_reconciliation_findings,
    extract_open_orders_by_symbol,
)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _extract_symbol_rows(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    normalized = _safe_str(symbol)
    return [row for row in rows if _safe_str(row.get("symbol")) == normalized]


def _symbol_findings(findings: dict[str, Any], symbol: str) -> dict[str, list[dict[str, Any]]]:
    mismatches = dict(findings.get("mismatches") or {})
    return {
        name: [
            item
            for item in list(items or [])
            if _safe_str(item.get("symbol")) == _safe_str(symbol)
        ]
        for name, items in mismatches.items()
    }


def _latest_guardrail_block(*, symbol: str) -> dict[str, Any] | None:
    rows = fetch_recent_trade_journal(
        limit=20,
        symbol=symbol,
        status="blocked",
    )
    if not rows:
        return None

    row = dict(rows[0])
    details = dict(row.get("details") or {})
    channel = _safe_str(row.get("channel"))
    reason = _safe_str(row.get("signal_reason") or row.get("exit_reason") or row.get("message"))
    details_message = _safe_str(details.get("reason") or details.get("message"))

    return {
        "created_at": _safe_str(row.get("created_at")),
        "channel": channel,
        "status": _safe_str(row.get("status")),
        "message": reason or details_message,
        "details": details,
    }


def build_symbol_operational_state(
    *,
    symbol: str,
    config: dict[str, Any],
    account_snapshot: dict[str, Any] | None,
    latest_prices: dict[str, float],
    runtime: dict[str, Any] | None = None,
    execution_orders: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_symbol = _safe_str(symbol)
    execution_orders = list(execution_orders or fetch_open_execution_orders())
    latest_filled_execution_orders = fetch_latest_filled_execution_orders_by_symbol()
    holdings_rows = build_live_holdings_snapshot(
        account_snapshot=account_snapshot,
        latest_prices=latest_prices,
        latest_filled_execution_orders=latest_filled_execution_orders,
    )
    holdings_row = next(
        (row for row in holdings_rows if _safe_str(row.get("symbol")) == normalized_symbol),
        {},
    )
    exchange_open_orders_by_symbol = extract_open_orders_by_symbol(account_snapshot)
    exchange_rows = list(exchange_open_orders_by_symbol.get(normalized_symbol, []))
    local_rows = _extract_symbol_rows(execution_orders, normalized_symbol)
    findings = collect_runtime_reconciliation_findings(
        execution_orders=execution_orders,
        live_holdings_rows=holdings_rows,
        account_snapshot=account_snapshot,
    )
    symbol_findings = _symbol_findings(findings, normalized_symbol)

    open_buy_orders = [row for row in local_rows if _safe_str(row.get("side")) == "buy"]
    open_sell_orders = [row for row in local_rows if _safe_str(row.get("side")) == "sell"]
    partial_fill_orders = [
        row for row in local_rows if _safe_str(row.get("state")) == "partially_filled"
    ]

    reserved_thb = 0.0
    for row in open_buy_orders:
        request_payload = dict(row.get("request_payload") or {})
        reserved_thb += _safe_float(request_payload.get("amt"))

    reserved_coin = _safe_float(holdings_row.get("reserved_qty", 0.0))
    available_coin = _safe_float(holdings_row.get("available_qty", 0.0))
    open_buy_count = len(open_buy_orders)
    open_sell_count = len(open_sell_orders)
    partial_fill = bool(partial_fill_orders or symbol_findings.get("partially_filled"))

    runtime = dict(runtime or {})
    live_auto_entry_enabled = bool(config.get("live_auto_entry_enabled", False))
    live_auto_exit_enabled = bool(config.get("live_auto_exit_enabled", False))
    manual_pause = bool(runtime.get("manual_pause", False))
    safety_pause = bool(runtime.get("safety_pause", False))

    entry_block_reasons: list[str] = []
    exit_block_reasons: list[str] = []
    review_reasons: list[str] = []

    if not live_auto_entry_enabled:
        entry_block_reasons.append("live auto-entry is OFF")
    if manual_pause:
        entry_block_reasons.append("manual pause is active")
        exit_block_reasons.append("manual pause is active")
    if safety_pause:
        entry_block_reasons.append("safety pause is active")
        exit_block_reasons.append("safety pause is active")
    if open_buy_count > 0:
        entry_block_reasons.append(f"{open_buy_count} open buy order(s) exist")
    if reserved_thb > 0:
        entry_block_reasons.append(f"reserved THB {reserved_thb:,.2f} is still linked to open buy order(s)")
    if not live_auto_exit_enabled:
        exit_block_reasons.append("live auto-exit is OFF")
    if open_sell_count > 0:
        exit_block_reasons.append(f"{open_sell_count} open sell order(s) exist")
    if reserved_coin > 0:
        exit_block_reasons.append(f"reserved coin {reserved_coin:,.8f} is still linked to the symbol")
    if partial_fill:
        exit_block_reasons.append("partial fill is still unresolved")

    recent_guardrail_block = _latest_guardrail_block(symbol=normalized_symbol)
    if recent_guardrail_block:
        message = _safe_str(recent_guardrail_block.get("message"))
        channel = _safe_str(recent_guardrail_block.get("channel"))
        if message:
            if "exit" in channel:
                exit_block_reasons.append(f"recent guardrail block: {message}")
            elif "entry" in channel:
                entry_block_reasons.append(f"recent guardrail block: {message}")

    unresolved_categories = [
        "partially_filled",
        "missing_on_exchange",
        "missing_locally",
        "orders_without_exchange_id",
        "reserved_without_open_order",
        "open_order_without_reserved",
        "unmanaged_live_holdings",
    ]
    for category in unresolved_categories:
        if symbol_findings.get(category):
            review_reasons.append(
                f"{category.replace('_', ' ')} needs review"
            )

    open_orders_meta = {}
    if isinstance(account_snapshot, dict):
        open_orders_meta = dict(account_snapshot.get("open_orders_meta") or {})
        if open_orders_meta.get("requires_symbol"):
            review_reasons.append("exchange open-orders coverage is partial")
        if open_orders_meta.get("error"):
            review_reasons.append("exchange open-orders query returned an error")

    review_required = bool(review_reasons)

    state_summary = (
        f"open buy {open_buy_count} | open sell {open_sell_count} | "
        f"reserved THB {reserved_thb:,.2f} | reserved coin {reserved_coin:,.8f}"
    )
    risk_summary = (
        f"entry {'blocked' if entry_block_reasons else 'clear'} | "
        f"exit {'blocked' if exit_block_reasons else 'clear'}"
    )

    return {
        "symbol": normalized_symbol,
        "state_summary": state_summary,
        "risk_summary": risk_summary,
        "available_coin": available_coin,
        "reserved_coin": reserved_coin,
        "reserved_thb": reserved_thb,
        "open_buy_count": open_buy_count,
        "open_sell_count": open_sell_count,
        "partial_fill": partial_fill,
        "entry_blocked": bool(entry_block_reasons),
        "exit_blocked": bool(exit_block_reasons),
        "entry_block_reasons": entry_block_reasons,
        "exit_block_reasons": exit_block_reasons,
        "review_required": review_required,
        "review_reasons": review_reasons,
        "recent_guardrail_block": recent_guardrail_block,
        "holdings_row": holdings_row,
        "exchange_open_order_count": len(exchange_rows),
        "exchange_open_orders": exchange_rows,
        "local_open_orders": local_rows,
        "findings": findings,
        "symbol_findings": symbol_findings,
    }
