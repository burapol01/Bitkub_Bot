from typing import Any

from clients.bitkub_private_client import BitkubPrivateClient, BitkubPrivateClientError


class OrderExecutionLockedError(Exception):
    pass


def _validate_positive(value: float, field_name: str):
    if float(value) <= 0:
        raise ValueError(f"{field_name} must be greater than 0")


def _validate_symbol(symbol: str):
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")


def build_place_bid_payload(
    *, symbol: str, amount_thb: float, rate: float, order_type: str = "limit"
) -> dict[str, Any]:
    _validate_symbol(symbol)
    _validate_positive(amount_thb, "amount_thb")
    _validate_positive(rate, "rate")
    if order_type != "limit":
        raise ValueError("order_type must currently be 'limit'")

    return {
        "sym": symbol,
        "amt": float(amount_thb),
        "rat": float(rate),
        "typ": order_type,
    }


def build_place_ask_payload(
    *, symbol: str, amount_coin: float, rate: float, order_type: str = "limit"
) -> dict[str, Any]:
    _validate_symbol(symbol)
    _validate_positive(amount_coin, "amount_coin")
    _validate_positive(rate, "rate")
    if order_type != "limit":
        raise ValueError("order_type must currently be 'limit'")

    return {
        "sym": symbol,
        "amt": float(amount_coin),
        "rat": float(rate),
        "typ": order_type,
    }


def build_cancel_order_payload(
    *, symbol: str, order_id: int | str, side: str
) -> dict[str, Any]:
    _validate_symbol(symbol)
    if not str(order_id).strip():
        raise ValueError("order_id must be provided")
    if side not in {"buy", "sell"}:
        raise ValueError("side must be either 'buy' or 'sell'")

    return {
        "sym": symbol,
        "id": order_id,
        "sd": side,
    }


def get_order_foundation_status(
    *, trading_mode: str, private_client: BitkubPrivateClient | None
) -> dict[str, Any]:
    credentials_loaded = bool(private_client and private_client.is_configured())

    if not credentials_loaded:
        return {
            "foundation_ready": False,
            "execution_locked": True,
            "status": "no private credentials loaded",
            "capabilities": [
                "place_bid=OFF",
                "place_ask=OFF",
                "cancel_order=OFF",
            ],
        }

    return {
        "foundation_ready": True,
        "execution_locked": trading_mode != "live",
        "status": (
            "order foundation ready, execution locked by mode"
            if trading_mode != "live"
            else "order foundation ready"
        ),
        "capabilities": [
            "place_bid=READY",
            "place_ask=READY",
            "cancel_order=READY",
        ],
    }


def raise_if_order_execution_locked(trading_mode: str):
    if trading_mode != "live":
        raise OrderExecutionLockedError(
            "Order execution is locked in this build. Foundation is prepared but live mode is not enabled."
        )


def _capture_probe(fetcher) -> dict[str, Any]:
    try:
        return {"ok": True, "data": fetcher(), "error": None}
    except BitkubPrivateClientError as e:
        return {"ok": False, "data": None, "error": str(e)}


def probe_order_foundation(
    *,
    client: BitkubPrivateClient | None,
    trading_mode: str,
    symbols: list[str],
) -> dict[str, Any]:
    foundation = get_order_foundation_status(
        trading_mode=trading_mode,
        private_client=client,
    )
    if client is None or not client.is_configured():
        return {
            **foundation,
            "open_orders": {},
            "order_history": {},
            "payload_examples": {},
        }

    open_orders: dict[str, Any] = {}
    order_history: dict[str, Any] = {}
    endpoint_variants: dict[str, Any] = {}
    payload_examples: dict[str, Any] = {}

    for symbol in sorted(symbols):
        open_orders[symbol] = _capture_probe(lambda symbol=symbol: client.get_open_orders(symbol))
        order_history[symbol] = _capture_probe(
            lambda symbol=symbol: client.get_order_history(symbol=symbol)
        )
        endpoint_variants[symbol] = {
            "open_orders": client.probe_open_orders_variants(symbol),
            "order_history": client.probe_order_history_variants(symbol),
        }
        place_bid_payload = build_place_bid_payload(
            symbol=symbol,
            amount_thb=100.0,
            rate=1.0,
        )
        place_ask_payload = build_place_ask_payload(
            symbol=symbol,
            amount_coin=0.0001,
            rate=1.0,
        )
        cancel_order_payload = build_cancel_order_payload(
            symbol=symbol,
            order_id=123456,
            side="buy",
        )
        payload_examples[symbol] = {
            "input_place_bid": place_bid_payload,
            "wire_place_bid": client.prepare_place_bid_payload(place_bid_payload),
            "input_place_ask": place_ask_payload,
            "wire_place_ask": client.prepare_place_ask_payload(place_ask_payload),
            "input_cancel_order": cancel_order_payload,
            "wire_cancel_order": client.prepare_cancel_order_payload(
                cancel_order_payload
            ),
        }

    return {
        **foundation,
        "open_orders": open_orders,
        "order_history": order_history,
        "endpoint_variants": endpoint_variants,
        "payload_examples": payload_examples,
    }
