from typing import Any

from clients.bitkub_private_client import BitkubPrivateClient, BitkubPrivateClientError
from services.order_service import build_place_ask_payload, build_place_bid_payload
from services.strategy_lab_service import build_coin_ranking

ORDER_STATE_CREATED = "created"
ORDER_STATE_SUBMITTED = "submitted"
ORDER_STATE_OPEN = "open"
ORDER_STATE_PARTIALLY_FILLED = "partially_filled"
ORDER_STATE_FILLED = "filled"
ORDER_STATE_CANCELED = "canceled"
ORDER_STATE_REJECTED = "rejected"
ORDER_STATE_FAILED = "failed"

TERMINAL_ORDER_STATES = {
    ORDER_STATE_FILLED,
    ORDER_STATE_CANCELED,
    ORDER_STATE_REJECTED,
    ORDER_STATE_FAILED,
}

ORDER_STATE_TRANSITIONS = {
    ORDER_STATE_CREATED: {
        ORDER_STATE_SUBMITTED,
        ORDER_STATE_REJECTED,
        ORDER_STATE_FAILED,
        ORDER_STATE_CANCELED,
    },
    ORDER_STATE_SUBMITTED: {
        ORDER_STATE_OPEN,
        ORDER_STATE_PARTIALLY_FILLED,
        ORDER_STATE_FILLED,
        ORDER_STATE_REJECTED,
        ORDER_STATE_FAILED,
        ORDER_STATE_CANCELED,
    },
    ORDER_STATE_OPEN: {
        ORDER_STATE_PARTIALLY_FILLED,
        ORDER_STATE_FILLED,
        ORDER_STATE_CANCELED,
        ORDER_STATE_FAILED,
    },
    ORDER_STATE_PARTIALLY_FILLED: {
        ORDER_STATE_FILLED,
        ORDER_STATE_CANCELED,
        ORDER_STATE_FAILED,
    },
    ORDER_STATE_FILLED: set(),
    ORDER_STATE_CANCELED: set(),
    ORDER_STATE_REJECTED: set(),
    ORDER_STATE_FAILED: set(),
}


class ExecutionStateError(Exception):
    pass


class LiveExecutionGuardrailError(Exception):
    pass


def _capability_status(capabilities: list[str] | None, name: str) -> str:
    if not capabilities:
        return "UNKNOWN"

    prefix = f"{name}="
    for item in capabilities:
        if item.startswith(prefix):
            return item.split("=", 1)[1]
    return "UNKNOWN"


def build_live_execution_guardrails(
    *,
    config: dict[str, Any],
    trading_mode: str,
    private_client: BitkubPrivateClient | None,
    private_api_capabilities: list[str] | None,
    manual_pause: bool,
    safety_pause: bool,
    total_realized_pnl_thb: float,
    available_balances: dict[str, float] | None,
    strategy_execution_wired: bool,
) -> dict[str, Any]:
    live_enabled = bool(config.get("live_execution_enabled", False))
    live_auto_entry_enabled = bool(config.get("live_auto_entry_enabled", False))
    live_auto_exit_enabled = bool(config.get("live_auto_exit_enabled", False))
    min_thb_balance = float(config.get("live_min_thb_balance", 0))
    daily_loss_limit = float(config.get("live_daily_loss_limit_thb", 0))
    max_order_thb = float(config.get("live_max_order_thb", 0))
    slippage_tolerance = float(config.get("live_slippage_tolerance_percent", 0))
    thb_balance = float((available_balances or {}).get("THB", 0.0))
    open_orders_status = _capability_status(private_api_capabilities, "open_orders")

    blocked_reasons: list[str] = []

    if trading_mode not in {"live", "shadow-live"}:
        blocked_reasons.append("trading mode is not live or shadow-live")
    if not live_enabled:
        blocked_reasons.append("live execution kill switch is OFF")
    if not strategy_execution_wired:
        blocked_reasons.append("strategy-driven live execution is not wired in this build")
    if private_client is None or not private_client.is_configured():
        blocked_reasons.append("private API credentials are not configured")
    if manual_pause:
        blocked_reasons.append("manual pause is active")
    if safety_pause:
        blocked_reasons.append("safety pause is active")
    if open_orders_status not in {"OK", "PARTIAL"}:
        blocked_reasons.append("open_orders capability is not ready")
    if thb_balance < min_thb_balance:
        blocked_reasons.append(
            f"THB available balance {thb_balance:,.2f} is below live_min_thb_balance {min_thb_balance:,.2f}"
        )
    if total_realized_pnl_thb <= -abs(daily_loss_limit):
        blocked_reasons.append(
            f"daily realized PnL {total_realized_pnl_thb:,.2f} THB has reached the live loss limit"
        )

    return {
        "mode": trading_mode,
        "shadow_live_mode": trading_mode == "shadow-live",
        "live_execution_enabled": live_enabled,
        "live_auto_entry_enabled": live_auto_entry_enabled,
        "live_auto_exit_enabled": live_auto_exit_enabled,
        "strategy_execution_wired": strategy_execution_wired,
        "ready": not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "open_orders_capability": open_orders_status,
        "thb_available_balance": thb_balance,
        "live_min_thb_balance": min_thb_balance,
        "live_daily_loss_limit_thb": daily_loss_limit,
        "live_max_order_thb": max_order_thb,
        "live_slippage_tolerance_percent": slippage_tolerance,
    }


def manual_live_order_summary(config: dict[str, Any]) -> dict[str, Any]:
    manual_order = dict(config.get("live_manual_order", {}))
    return {
        "enabled": bool(manual_order.get("enabled", False)),
        "symbol": str(manual_order.get("symbol", "")),
        "side": str(manual_order.get("side", "")),
        "order_type": str(manual_order.get("order_type", "")),
        "amount_thb": float(manual_order.get("amount_thb", 0.0)),
        "amount_coin": float(manual_order.get("amount_coin", 0.0)),
        "rate": float(manual_order.get("rate", 0.0)),
    }


def build_manual_live_order_request(
    *,
    config: dict[str, Any],
    rules: dict[str, Any],
) -> dict[str, Any]:
    summary = manual_live_order_summary(config)
    symbol = summary["symbol"]
    side = summary["side"]
    order_type = summary["order_type"]
    rate = summary["rate"]

    if not summary["enabled"]:
        raise LiveExecutionGuardrailError("live_manual_order.enabled is false")
    if symbol not in rules:
        raise LiveExecutionGuardrailError(
            f"live_manual_order.symbol is not present in config rules: {symbol}"
        )

    if side == "buy":
        request_payload = build_place_bid_payload(
            symbol=symbol,
            amount_thb=summary["amount_thb"],
            rate=rate,
            order_type=order_type,
        )
        requested_notional_thb = float(summary["amount_thb"])
    elif side == "sell":
        request_payload = build_place_ask_payload(
            symbol=symbol,
            amount_coin=summary["amount_coin"],
            rate=rate,
            order_type=order_type,
        )
        requested_notional_thb = float(summary["amount_coin"]) * float(rate)
    else:
        raise LiveExecutionGuardrailError(f"unsupported live_manual_order.side: {side}")

    return {
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "rate": rate,
        "request_payload": request_payload,
        "requested_notional_thb": requested_notional_thb,
    }


def validate_manual_live_order_guardrails(
    *,
    request: dict[str, Any],
    guardrails: dict[str, Any],
    available_balances: dict[str, float],
) -> list[str]:
    side = str(request["side"])
    reasons = [
        reason
        for reason in guardrails.get("blocked_reasons", [])
        if reason != "strategy-driven live execution is not wired in this build"
        and not (
            side == "sell"
            and reason.startswith("THB available balance ")
        )
    ]
    requested_notional = float(request["requested_notional_thb"])
    max_order_thb = float(guardrails.get("live_max_order_thb", 0.0))
    symbol = str(request["symbol"])
    rate = float(request["rate"])

    if requested_notional > max_order_thb:
        reasons.append(
            f"requested order notional {requested_notional:,.2f} THB exceeds live_max_order_thb {max_order_thb:,.2f}"
        )

    if side == "buy":
        thb_available = float(available_balances.get("THB", 0.0))
        if thb_available < requested_notional:
            reasons.append(
                f"THB available balance {thb_available:,.2f} is below requested buy amount {requested_notional:,.2f}"
            )
    elif side == "sell":
        asset = symbol.split("_", 1)[1] if "_" in symbol else symbol
        coin_amount = float(request["request_payload"]["amt"])
        asset_available = float(available_balances.get(asset, 0.0))
        if asset_available < coin_amount:
            reasons.append(
                f"{asset} available balance {asset_available:,.8f} is below requested sell amount {coin_amount:,.8f}"
            )
        if rate <= 0:
            reasons.append("sell rate must be greater than 0")

    return reasons


def build_live_sell_request(
    *,
    symbol: str,
    amount_coin: float,
    rate: float,
    order_type: str = "limit",
) -> dict[str, Any]:
    request_payload = build_place_ask_payload(
        symbol=symbol,
        amount_coin=amount_coin,
        rate=rate,
        order_type=order_type,
    )
    return {
        "symbol": symbol,
        "side": "sell",
        "order_type": order_type,
        "rate": rate,
        "request_payload": request_payload,
        "requested_notional_thb": float(amount_coin) * float(rate),
    }


def build_live_buy_request(
    *,
    symbol: str,
    amount_thb: float,
    rate: float,
    order_type: str = "limit",
) -> dict[str, Any]:
    request_payload = build_place_bid_payload(
        symbol=symbol,
        amount_thb=amount_thb,
        rate=rate,
        order_type=order_type,
    )
    return {
        "symbol": symbol,
        "side": "buy",
        "order_type": order_type,
        "rate": rate,
        "request_payload": request_payload,
        "requested_notional_thb": float(amount_thb),
    }


def validate_live_buy_request_guardrails(
    *,
    request: dict[str, Any],
    guardrails: dict[str, Any],
    available_balances: dict[str, float],
    latest_price: float,
) -> list[str]:
    reasons = [
        reason
        for reason in guardrails.get("blocked_reasons", [])
        if reason != "strategy-driven live execution is not wired in this build"
    ]
    requested_notional = float(request["requested_notional_thb"])
    max_order_thb = float(guardrails.get("live_max_order_thb", 0.0))
    slippage_tolerance = float(
        guardrails.get("live_slippage_tolerance_percent", 0.0)
    )
    thb_available = float(available_balances.get("THB", 0.0))
    request_rate = float(request["rate"])

    if requested_notional > max_order_thb:
        reasons.append(
            f"requested order notional {requested_notional:,.2f} THB exceeds live_max_order_thb {max_order_thb:,.2f}"
        )

    if thb_available < requested_notional:
        reasons.append(
            f"THB available balance {thb_available:,.2f} is below requested buy amount {requested_notional:,.2f}"
        )

    if latest_price <= 0:
        reasons.append("latest price must be greater than 0 for live entry")
    else:
        slippage_percent = abs((request_rate - latest_price) / latest_price) * 100
        if slippage_percent > slippage_tolerance:
            reasons.append(
                f"buy rate deviates {slippage_percent:.2f}% from latest price, above live_slippage_tolerance_percent {slippage_tolerance:.2f}%"
            )

    return reasons


def validate_live_sell_request_guardrails(
    *,
    request: dict[str, Any],
    guardrails: dict[str, Any],
    available_balances: dict[str, float],
    latest_price: float,
) -> list[str]:
    reasons = [
        reason
        for reason in guardrails.get("blocked_reasons", [])
        if reason != "strategy-driven live execution is not wired in this build"
        and not reason.startswith("THB available balance ")
    ]
    requested_notional = float(request["requested_notional_thb"])
    max_order_thb = float(guardrails.get("live_max_order_thb", 0.0))
    slippage_tolerance = float(
        guardrails.get("live_slippage_tolerance_percent", 0.0)
    )
    symbol = str(request["symbol"])
    asset = symbol.split("_", 1)[1] if "_" in symbol else symbol
    coin_amount = float(request["request_payload"]["amt"])
    asset_available = float(available_balances.get(asset, 0.0))
    request_rate = float(request["rate"])

    if requested_notional > max_order_thb:
        reasons.append(
            f"requested order notional {requested_notional:,.2f} THB exceeds live_max_order_thb {max_order_thb:,.2f}"
        )

    if asset_available < coin_amount:
        reasons.append(
            f"{asset} available balance {asset_available:,.8f} is below requested sell amount {coin_amount:,.8f}"
        )

    if latest_price <= 0:
        reasons.append("latest price must be greater than 0 for live exit")
    else:
        slippage_percent = abs((request_rate - latest_price) / latest_price) * 100
        if slippage_percent > slippage_tolerance:
            reasons.append(
                f"sell rate deviates {slippage_percent:.2f}% from latest price, above live_slippage_tolerance_percent {slippage_tolerance:.2f}%"
            )

    return reasons


def evaluate_live_entry_candidates(
    *,
    config: dict[str, Any],
    rules: dict[str, Any],
    entry_signal_rows: list[dict[str, Any]],
    live_holdings_rows: list[dict[str, Any]],
    open_execution_orders: list[dict[str, Any]],
    exchange_open_orders_by_symbol: dict[str, list[dict[str, Any]]],
    unsupported_symbols: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not entry_signal_rows:
        return {
            "candidates": [],
            "rejected": [],
            "ranking_errors": [],
            "ranking_context": {},
        }

    open_execution_symbols = {
        str(order.get("symbol", ""))
        for order in open_execution_orders
        if str(order.get("state", "")) not in TERMINAL_ORDER_STATES
    }
    holding_symbols = {
        str(row.get("symbol", ""))
        for row in live_holdings_rows
        if float(row.get("available_qty", 0.0) or 0.0) > 0
        or float(row.get("reserved_qty", 0.0) or 0.0) > 0
    }
    ranking_resolution = str(config.get("live_auto_entry_rank_resolution", "240"))
    ranking_lookback_days = int(config.get("live_auto_entry_rank_lookback_days", 14))
    min_score = float(config.get("live_auto_entry_min_score", 0.0))
    require_ranking = bool(config.get("live_auto_entry_require_ranking", False))
    allowed_biases = {
        str(value).strip().lower()
        for value in config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"])
        if str(value).strip()
    } or {"bullish", "mixed"}
    unsupported_symbols = {
        str(symbol): str(reason)
        for symbol, reason in (unsupported_symbols or {}).items()
        if str(symbol)
    }

    ranking_result = build_coin_ranking(
        symbols=sorted(rules.keys()),
        resolution=ranking_resolution,
        lookback_days=ranking_lookback_days,
    )
    ranking_lookup = {
        str(row["symbol"]): row
        for row in ranking_result.get("rows", [])
    }
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for row in entry_signal_rows:
        symbol = str(row.get("symbol", ""))
        rejection_reasons: list[str] = []
        if symbol not in rules:
            rejection_reasons.append("symbol is not present in config rules")
        if symbol in unsupported_symbols:
            rejection_reasons.append(unsupported_symbols[symbol])
        if symbol in open_execution_symbols:
            rejection_reasons.append("symbol already has an open execution order")
        if symbol in holding_symbols:
            rejection_reasons.append("symbol already has live holdings or reserved balance")
        if exchange_open_orders_by_symbol.get(symbol):
            rejection_reasons.append("symbol already has an exchange open order")

        rule = rules.get(symbol)
        latest_price = float(row.get("latest_price", 0.0) or 0.0)
        if latest_price <= 0:
            rejection_reasons.append("latest price is not valid")

        if rule is None:
            rejected.append(
                {
                    "symbol": symbol,
                    "signal_reason": str(row.get("signal_reason") or "BUY_ZONE_ENTRY"),
                    "reasons": rejection_reasons,
                }
            )
            continue

        ranking_row = ranking_lookup.get(symbol)
        if ranking_row is None:
            if require_ranking:
                rejection_reasons.append("symbol does not have enough stored candles for ranking")
            ranking_score = None
            trend_bias = None
        else:
            ranking_score = float(ranking_row.get("score", 0.0) or 0.0)
            trend_bias = str(ranking_row.get("trend_bias") or "").lower()
            if ranking_score < min_score:
                rejection_reasons.append(
                    f"ranking score {ranking_score:.2f} is below live_auto_entry_min_score {min_score:.2f}"
                )
            if trend_bias and trend_bias not in allowed_biases:
                rejection_reasons.append(
                    "trend bias "
                    f"{trend_bias} is not allowed by live_auto_entry_allowed_biases"
                )

        buy_below = float(rule["buy_below"])
        entry_discount_percent = (
            max(0.0, (buy_below - latest_price) / buy_below) * 100.0
            if buy_below > 0
            else 0.0
        )

        if rejection_reasons:
            rejected.append(
                {
                    "symbol": symbol,
                    "signal_reason": str(row.get("signal_reason") or "BUY_ZONE_ENTRY"),
                    "latest_price": latest_price,
                    "buy_below": buy_below,
                    "entry_discount_percent": entry_discount_percent,
                    "ranking_score": ranking_score,
                    "trend_bias": trend_bias,
                    "reasons": rejection_reasons,
                }
            )
            continue

        candidates.append(
            {
                "symbol": symbol,
                "amount_thb": float(rule["budget_thb"]),
                "rate": latest_price,
                "latest_price": latest_price,
                "signal_reason": str(row.get("signal_reason") or "BUY_ZONE_ENTRY"),
                "buy_below": buy_below,
                "entry_discount_percent": entry_discount_percent,
                "ranking_score": ranking_score,
                "trend_bias": trend_bias,
                "ranking_resolution": ranking_resolution,
                "ranking_lookback_days": ranking_lookback_days,
            }
        )

    candidates.sort(
        key=lambda item: (
            float(item.get("ranking_score") or -1.0),
            float(item.get("entry_discount_percent") or 0.0),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )
    rejected.sort(key=lambda item: str(item.get("symbol") or ""))

    return {
        "candidates": candidates,
        "rejected": rejected,
        "ranking_errors": list(ranking_result.get("errors", [])),
        "ranking_context": {
            "resolution": ranking_resolution,
            "lookback_days": ranking_lookback_days,
            "min_score": min_score,
            "require_ranking": require_ranking,
            "allowed_biases": sorted(allowed_biases),
        },
    }


def evaluate_live_exit_candidates(
    *,
    rules: dict[str, Any],
    live_holdings_rows: list[dict[str, Any]],
    open_execution_orders: list[dict[str, Any]],
    exchange_open_orders_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    open_execution_symbols = {
        str(order.get("symbol", ""))
        for order in open_execution_orders
        if str(order.get("state", "")) not in TERMINAL_ORDER_STATES
    }
    candidates: list[dict[str, Any]] = []

    for row in live_holdings_rows:
        symbol = str(row.get("symbol", ""))
        if symbol not in rules:
            continue
        if symbol in open_execution_symbols:
            continue
        if exchange_open_orders_by_symbol.get(symbol):
            continue

        available_qty = float(row.get("available_qty", 0.0) or 0.0)
        latest_price = row.get("latest_price")
        last_execution_side = str(row.get("last_execution_side") or "")
        last_execution_rate = row.get("last_execution_rate")

        if available_qty <= 0:
            continue
        if latest_price is None or float(latest_price) <= 0:
            continue
        if last_execution_side != "buy":
            continue
        if last_execution_rate is None or float(last_execution_rate) <= 0:
            continue

        rule = rules[symbol]
        entry_rate = float(last_execution_rate)
        current_price = float(latest_price)
        stop_loss_price = entry_rate * (1 - float(rule["stop_loss_percent"]) / 100)
        take_profit_price = entry_rate * (
            1 + float(rule["take_profit_percent"]) / 100
        )
        sell_above = float(rule["sell_above"])
        exit_reason = None
        request_rate = None

        if current_price <= stop_loss_price:
            exit_reason = "STOP_LOSS"
            request_rate = current_price
        elif current_price >= sell_above:
            exit_reason = "SELL_ZONE"
            request_rate = sell_above
        elif current_price >= take_profit_price:
            exit_reason = "TAKE_PROFIT"
            request_rate = current_price

        if exit_reason is None or request_rate is None:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "amount_coin": available_qty,
                "rate": request_rate,
                "latest_price": current_price,
                "entry_rate": entry_rate,
                "exit_reason": exit_reason,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
                "sell_above": sell_above,
            }
        )

    return candidates


def build_live_order_record(
    *,
    created_at: str,
    symbol: str,
    side: str,
    order_type: str,
    request_payload: dict[str, Any],
    guardrails: dict[str, Any],
) -> dict[str, Any]:
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if order_type not in {"limit", "market"}:
        raise ValueError("order_type must be limit or market")

    return {
        "created_at": created_at,
        "updated_at": created_at,
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "state": ORDER_STATE_CREATED,
        "request_payload": request_payload,
        "response_payload": None,
        "guardrails": guardrails,
        "exchange_order_id": None,
        "exchange_client_id": None,
        "message": "execution order record created",
    }


def transition_live_order_state(
    *,
    order_record: dict[str, Any],
    new_state: str,
    occurred_at: str,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
    response_payload: dict[str, Any] | None = None,
    exchange_order_id: str | None = None,
    exchange_client_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_state = str(order_record["state"])
    allowed_transitions = ORDER_STATE_TRANSITIONS.get(current_state, set())

    if new_state != current_state and new_state not in allowed_transitions:
        raise ExecutionStateError(
            f"invalid live order state transition: {current_state} -> {new_state}"
        )

    updated_record = dict(order_record)
    updated_record["updated_at"] = occurred_at
    updated_record["state"] = new_state
    updated_record["message"] = message
    if response_payload is not None:
        updated_record["response_payload"] = response_payload
    if exchange_order_id is not None:
        updated_record["exchange_order_id"] = exchange_order_id
    if exchange_client_id is not None:
        updated_record["exchange_client_id"] = exchange_client_id

    event = {
        "created_at": occurred_at,
        "from_state": current_state,
        "to_state": new_state,
        "event_type": event_type,
        "message": message,
        "details": details or {},
    }
    return updated_record, event


def _unwrap_exchange_result(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "y"}


def map_exchange_order_to_state(
    payload: dict[str, Any] | Any,
    *,
    default_open: bool = False,
) -> str:
    result = _unwrap_exchange_result(payload)
    if not isinstance(result, dict):
        return ORDER_STATE_OPEN if default_open else ORDER_STATE_OPEN

    status = str(result.get("status", "")).strip().lower()
    partially_filled = _to_bool(result.get("partial_filled", False))
    filled_amount = _to_float(result.get("filled"))
    total_amount = _to_float(result.get("total"))
    remaining_amount = _to_float(result.get("remaining"))

    if status == "filled":
        return ORDER_STATE_FILLED
    if status in {"cancelled", "canceled"}:
        return ORDER_STATE_CANCELED
    if status == "unfilled" and partially_filled:
        return ORDER_STATE_PARTIALLY_FILLED
    if status == "unfilled":
        return ORDER_STATE_OPEN
    if (
        filled_amount is not None
        and total_amount is not None
        and total_amount > 0
        and filled_amount >= total_amount
    ):
        return ORDER_STATE_FILLED
    if (
        partially_filled
        or (
            filled_amount is not None
            and total_amount is not None
            and total_amount > 0
            and 0 < filled_amount < total_amount
        )
        or (
            remaining_amount is not None
            and total_amount is not None
            and total_amount > 0
            and 0 < remaining_amount < total_amount
        )
    ):
        return ORDER_STATE_PARTIALLY_FILLED
    return ORDER_STATE_OPEN


def map_order_info_to_state(order_info: dict[str, Any]) -> str:
    return map_exchange_order_to_state(order_info)


def _extract_exchange_order_rows(payload: Any) -> list[dict[str, Any]]:
    result = _unwrap_exchange_result(payload)
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def _find_matching_exchange_order_row(
    payload: Any,
    *,
    exchange_order_id: str,
) -> dict[str, Any] | None:
    target = str(exchange_order_id or "").strip()
    if not target:
        return None

    for row in _extract_exchange_order_rows(payload):
        for key in ("id", "order_id", "first", "parent", "last"):
            if str(row.get(key) or "").strip() == target:
                return row
    return None


def _is_missing_order_error_message(error: str | None) -> bool:
    normalized = str(error or "").strip().lower()
    if not normalized:
        return False
    if "endpoint not found" in normalized or "requires sym" in normalized:
        return False

    markers = (
        "not found",
        "order not found",
        "unknown order",
        "invalid order",
        "does not exist",
        "no order",
    )
    return any(marker in normalized for marker in markers)


def submit_manual_live_order(
    *,
    client: BitkubPrivateClient,
    config: dict[str, Any],
    rules: dict[str, Any],
    guardrails: dict[str, Any],
    available_balances: dict[str, float],
    created_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    request = build_manual_live_order_request(config=config, rules=rules)
    validation_errors = validate_manual_live_order_guardrails(
        request=request,
        guardrails=guardrails,
        available_balances=available_balances,
    )
    if validation_errors:
        raise LiveExecutionGuardrailError("; ".join(validation_errors))

    order_record = build_live_order_record(
        created_at=created_at,
        symbol=request["symbol"],
        side=request["side"],
        order_type=request["order_type"],
        request_payload=request["request_payload"],
        guardrails=guardrails,
    )
    events: list[dict[str, Any]] = []

    if request["side"] == "buy":
        submit_response = client.place_bid(request["request_payload"])
    else:
        submit_response = client.place_ask(request["request_payload"])

    submit_result = submit_response.get("result", {}) if isinstance(submit_response, dict) else {}
    exchange_order_id = str(submit_result.get("id", "")) or None
    exchange_client_id = str(submit_result.get("ci", "")) or None

    order_record, submitted_event = transition_live_order_state(
        order_record=order_record,
        new_state=ORDER_STATE_SUBMITTED,
        occurred_at=created_at,
        event_type="submit_response",
        message="manual live order submitted to Bitkub",
        details={"request": request},
        response_payload=submit_response,
        exchange_order_id=exchange_order_id,
        exchange_client_id=exchange_client_id,
    )
    events.append(submitted_event)

    if exchange_order_id is not None:
        order_info = client.get_order_info(
            order_id=exchange_order_id,
            symbol=request["symbol"],
            side=request["side"],
        )
        mapped_state = map_order_info_to_state(order_info)
        order_record, order_info_event = transition_live_order_state(
            order_record=order_record,
            new_state=mapped_state,
            occurred_at=created_at,
            event_type="order_info_refresh",
            message="manual live order refreshed from Bitkub order_info",
            response_payload=order_info,
            exchange_order_id=exchange_order_id,
            exchange_client_id=exchange_client_id,
        )
        events.append(order_info_event)

    return order_record, events


def refresh_live_order_from_exchange(
    *,
    client: BitkubPrivateClient,
    order_record: dict[str, Any],
    occurred_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    exchange_order_id = order_record.get("exchange_order_id")
    if not exchange_order_id:
        raise LiveExecutionGuardrailError(
            "execution order has no exchange_order_id and cannot be refreshed"
        )

    symbol = str(order_record["symbol"])
    side = str(order_record["side"])
    refresh_payload: dict[str, Any] | Any
    refresh_event_type = "order_info_refresh"
    refresh_message = "live order refreshed from Bitkub order_info"

    try:
        order_info = client.get_order_info(
            order_id=exchange_order_id,
            symbol=symbol,
            side=side,
        )
        mapped_state = map_order_info_to_state(order_info)
        refresh_payload = order_info
    except BitkubPrivateClientError as order_info_error:
        open_orders_payload: Any = None
        open_orders_error: str | None = None
        order_history_payload: Any = None
        order_history_error: str | None = None

        try:
            open_orders_payload = client.get_open_orders(symbol=symbol)
            matching_open_order = _find_matching_exchange_order_row(
                open_orders_payload,
                exchange_order_id=str(exchange_order_id),
            )
            if matching_open_order is not None:
                mapped_state = map_exchange_order_to_state(
                    matching_open_order,
                    default_open=True,
                )
                refresh_payload = {
                    "probe": "open_orders",
                    "order_info_error": str(order_info_error),
                    "open_orders": open_orders_payload,
                    "matched_order": matching_open_order,
                }
                refresh_event_type = "open_orders_refresh"
                refresh_message = "live order refreshed from Bitkub open_orders fallback"
            else:
                mapped_state = None
        except BitkubPrivateClientError as open_orders_probe_error:
            mapped_state = None
            open_orders_error = str(open_orders_probe_error)

        if mapped_state is None:
            try:
                order_history_payload = client.get_order_history(symbol=symbol, limit=50)
                matching_history_order = _find_matching_exchange_order_row(
                    order_history_payload,
                    exchange_order_id=str(exchange_order_id),
                )
                if matching_history_order is not None:
                    mapped_state = map_exchange_order_to_state(matching_history_order)
                    refresh_payload = {
                        "probe": "order_history",
                        "order_info_error": str(order_info_error),
                        "open_orders_error": open_orders_error,
                        "order_history": order_history_payload,
                        "matched_order": matching_history_order,
                    }
                    refresh_event_type = "order_history_refresh"
                    refresh_message = "live order refreshed from Bitkub order_history fallback"
            except BitkubPrivateClientError as order_history_probe_error:
                order_history_error = str(order_history_probe_error)

        if mapped_state is None:
            no_open_order_match = open_orders_payload is not None and (
                _find_matching_exchange_order_row(
                    open_orders_payload,
                    exchange_order_id=str(exchange_order_id),
                )
                is None
            )
            no_history_match = order_history_payload is not None and (
                _find_matching_exchange_order_row(
                    order_history_payload,
                    exchange_order_id=str(exchange_order_id),
                )
                is None
            )
            if _is_missing_order_error_message(str(order_info_error)) and no_open_order_match and no_history_match:
                mapped_state = ORDER_STATE_CANCELED
                refresh_payload = {
                    "probe": "exchange_absence_inference",
                    "order_info_error": str(order_info_error),
                    "open_orders": open_orders_payload,
                    "open_orders_error": open_orders_error,
                    "order_history": order_history_payload,
                    "order_history_error": order_history_error,
                }
                refresh_event_type = "exchange_absence_refresh"
                refresh_message = (
                    "live order no longer appears in Bitkub order_info/open_orders/order_history; marked canceled"
                )

        if mapped_state is None:
            raise order_info_error

    updated_record, refresh_event = transition_live_order_state(
        order_record=order_record,
        new_state=mapped_state,
        occurred_at=occurred_at,
        event_type=refresh_event_type,
        message=refresh_message,
        response_payload=refresh_payload,
        exchange_order_id=str(exchange_order_id),
        exchange_client_id=order_record.get("exchange_client_id"),
    )
    return updated_record, [refresh_event]


def cancel_live_order(
    *,
    client: BitkubPrivateClient,
    order_record: dict[str, Any],
    occurred_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    exchange_order_id = order_record.get("exchange_order_id")
    if not exchange_order_id:
        raise LiveExecutionGuardrailError(
            "execution order has no exchange_order_id and cannot be cancelled"
        )

    cancel_payload = {
        "sym": str(order_record["symbol"]),
        "id": exchange_order_id,
        "sd": str(order_record["side"]),
    }
    cancel_response = client.cancel_order(cancel_payload)
    events: list[dict[str, Any]] = []

    interim_record, cancel_event = transition_live_order_state(
        order_record=order_record,
        new_state=str(order_record["state"]),
        occurred_at=occurred_at,
        event_type="cancel_request",
        message="live order cancel request submitted to Bitkub",
        response_payload=cancel_response,
        exchange_order_id=str(exchange_order_id),
        exchange_client_id=order_record.get("exchange_client_id"),
    )
    events.append(cancel_event)

    refreshed_record, refresh_events = refresh_live_order_from_exchange(
        client=client,
        order_record=interim_record,
        occurred_at=occurred_at,
    )
    events.extend(refresh_events)
    return refreshed_record, events


def submit_auto_live_entry_order(
    *,
    client: BitkubPrivateClient,
    symbol: str,
    amount_thb: float,
    rate: float,
    latest_price: float,
    signal_reason: str,
    guardrails: dict[str, Any],
    available_balances: dict[str, float],
    created_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    request = build_live_buy_request(
        symbol=symbol,
        amount_thb=amount_thb,
        rate=rate,
        order_type="limit",
    )
    validation_errors = validate_live_buy_request_guardrails(
        request=request,
        guardrails=guardrails,
        available_balances=available_balances,
        latest_price=latest_price,
    )
    if validation_errors:
        raise LiveExecutionGuardrailError("; ".join(validation_errors))

    order_record = build_live_order_record(
        created_at=created_at,
        symbol=request["symbol"],
        side=request["side"],
        order_type=request["order_type"],
        request_payload=request["request_payload"],
        guardrails=guardrails,
    )
    events: list[dict[str, Any]] = []

    submit_response = client.place_bid(request["request_payload"])
    submit_result = submit_response.get("result", {}) if isinstance(submit_response, dict) else {}
    exchange_order_id = str(submit_result.get("id", "")) or None
    exchange_client_id = str(submit_result.get("ci", "")) or None

    order_record, submitted_event = transition_live_order_state(
        order_record=order_record,
        new_state=ORDER_STATE_SUBMITTED,
        occurred_at=created_at,
        event_type="auto_entry_submit",
        message=f"auto live entry submitted to Bitkub ({signal_reason})",
        details={
            "signal_reason": signal_reason,
            "latest_price": latest_price,
            "request": request,
        },
        response_payload=submit_response,
        exchange_order_id=exchange_order_id,
        exchange_client_id=exchange_client_id,
    )
    events.append(submitted_event)

    if exchange_order_id is not None:
        order_info = client.get_order_info(
            order_id=exchange_order_id,
            symbol=request["symbol"],
            side=request["side"],
        )
        mapped_state = map_order_info_to_state(order_info)
        order_record, order_info_event = transition_live_order_state(
            order_record=order_record,
            new_state=mapped_state,
            occurred_at=created_at,
            event_type="order_info_refresh",
            message=f"auto live entry refreshed from Bitkub order_info ({signal_reason})",
            response_payload=order_info,
            exchange_order_id=exchange_order_id,
            exchange_client_id=exchange_client_id,
        )
        events.append(order_info_event)

    return order_record, events


def submit_auto_live_exit_order(
    *,
    client: BitkubPrivateClient,
    symbol: str,
    amount_coin: float,
    rate: float,
    latest_price: float,
    exit_reason: str,
    guardrails: dict[str, Any],
    available_balances: dict[str, float],
    created_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    request = build_live_sell_request(
        symbol=symbol,
        amount_coin=amount_coin,
        rate=rate,
        order_type="limit",
    )
    validation_errors = validate_live_sell_request_guardrails(
        request=request,
        guardrails=guardrails,
        available_balances=available_balances,
        latest_price=latest_price,
    )
    if validation_errors:
        raise LiveExecutionGuardrailError("; ".join(validation_errors))

    order_record = build_live_order_record(
        created_at=created_at,
        symbol=request["symbol"],
        side=request["side"],
        order_type=request["order_type"],
        request_payload=request["request_payload"],
        guardrails=guardrails,
    )
    events: list[dict[str, Any]] = []

    submit_response = client.place_ask(request["request_payload"])
    submit_result = submit_response.get("result", {}) if isinstance(submit_response, dict) else {}
    exchange_order_id = str(submit_result.get("id", "")) or None
    exchange_client_id = str(submit_result.get("ci", "")) or None

    order_record, submitted_event = transition_live_order_state(
        order_record=order_record,
        new_state=ORDER_STATE_SUBMITTED,
        occurred_at=created_at,
        event_type="auto_exit_submit",
        message=f"auto live exit submitted to Bitkub ({exit_reason})",
        details={
            "exit_reason": exit_reason,
            "latest_price": latest_price,
            "request": request,
        },
        response_payload=submit_response,
        exchange_order_id=exchange_order_id,
        exchange_client_id=exchange_client_id,
    )
    events.append(submitted_event)

    if exchange_order_id is not None:
        order_info = client.get_order_info(
            order_id=exchange_order_id,
            symbol=request["symbol"],
            side=request["side"],
        )
        mapped_state = map_order_info_to_state(order_info)
        order_record, order_info_event = transition_live_order_state(
            order_record=order_record,
            new_state=mapped_state,
            occurred_at=created_at,
            event_type="order_info_refresh",
            message=f"auto live exit refreshed from Bitkub order_info ({exit_reason})",
            response_payload=order_info,
            exchange_order_id=exchange_order_id,
            exchange_client_id=exchange_client_id,
        )
        events.append(order_info_event)

    return order_record, events
