from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import requests

from config import load_config
from services.api_retry_service import (
    classify_retry_error,
    get_retry_policy,
    log_api_retry_event,
    retry_delay_seconds,
    should_retry,
)
from utils.time_utils import now_dt


def _base_url() -> str:
    config = load_config()
    return str(config["base_url"]).rstrip("/")


def _get_json(
    *,
    path: str,
    params: dict[str, Any] | None = None,
    timeout_seconds: int,
    action: str,
) -> Any:
    policy_name = "market_public_read"
    policy = get_retry_policy(policy_name)
    last_exception: Exception | None = None

    for attempt in range(1, int(policy.max_attempts) + 1):
        try:
            response = requests.get(f"{_base_url()}{path}", params=params, timeout=timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if attempt > 1:
                log_api_retry_event(
                    endpoint=path,
                    action=action,
                    attempt=attempt,
                    policy_name=policy_name,
                    classification={
                        "category": "success_after_retry",
                        "retryable": False,
                        "ambiguous": False,
                        "reason": "request succeeded after retry",
                    },
                    outcome="succeeded_after_retry",
                    status_code=response.status_code,
                )
            return payload
        except requests.HTTPError as e:
            last_exception = e
            status_code = e.response.status_code if e.response is not None else None
            response_text = e.response.text if e.response is not None else None
            classification = classify_retry_error(
                error=e,
                status_code=status_code,
                response_text=response_text,
            )
        except ValueError as e:
            last_exception = e
            classification = classify_retry_error(
                error=e,
                error_message="Bitkub public API returned invalid JSON.",
            )
            status_code = None
        except requests.RequestException as e:
            last_exception = e
            classification = classify_retry_error(error=e)
            status_code = None

        if should_retry(
            policy_name=policy_name,
            classification=classification,
            attempt=attempt,
        ):
            delay_seconds = retry_delay_seconds(
                policy_name=policy_name,
                attempt=attempt,
            )
            log_api_retry_event(
                endpoint=path,
                action=action,
                attempt=attempt,
                policy_name=policy_name,
                classification=classification,
                outcome="retrying",
                delay_seconds=delay_seconds,
                status_code=status_code,
            )
            time.sleep(delay_seconds)
            continue

        log_api_retry_event(
            endpoint=path,
            action=action,
            attempt=attempt,
            policy_name=policy_name,
            classification=classification,
            outcome="give_up",
            status_code=status_code,
        )
        if isinstance(classification.get("reason"), str) and classification["reason"]:
            raise requests.RequestException(classification["reason"])
        if last_exception is not None:
            raise last_exception
        raise requests.RequestException("Bitkub public API request failed.")

    raise requests.RequestException("Bitkub public API request failed.")


def get_ticker() -> dict[str, Any]:
    payload = _get_json(
        path="/api/market/ticker",
        timeout_seconds=10,
        action="get_ticker",
    )
    return payload if isinstance(payload, dict) else {}


def to_tradingview_symbol(symbol: str) -> str:
    parts = str(symbol).split("_")
    if len(parts) != 2:
        return str(symbol).upper()
    quote, base = parts
    return f"{base.upper()}_{quote.upper()}"


def from_tradingview_symbol(symbol: str) -> str:
    parts = str(symbol).split("_")
    if len(parts) != 2:
        return str(symbol).upper()
    base, quote = parts
    return f"{quote.upper()}_{base.upper()}"


def get_market_symbols_v3() -> list[dict[str, Any]]:
    payload = _get_json(
        path="/api/v3/market/symbols",
        timeout_seconds=10,
        action="get_market_symbols_v3",
    )
    if isinstance(payload, dict):
        return list(payload.get("result", []))
    return []


def get_tradingview_history(
    *,
    symbol: str,
    resolution: str,
    from_ts: int,
    to_ts: int,
) -> dict[str, Any]:
    params = {
        "symbol": to_tradingview_symbol(symbol),
        "resolution": str(resolution),
        "from": int(from_ts),
        "to": int(to_ts),
    }
    payload = _get_json(
        path="/tradingview/history",
        params=params,
        timeout_seconds=20,
        action="get_tradingview_history",
    )
    return payload if isinstance(payload, dict) else {}


def build_history_window(*, days: int) -> tuple[int, int]:
    now = now_dt()
    start = now - timedelta(days=int(days))
    return int(start.timestamp()), int(now.timestamp())
