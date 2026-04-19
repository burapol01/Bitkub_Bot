from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import requests

from services.db_service import insert_runtime_event
from utils.time_utils import now_text


@dataclass(frozen=True)
class RetryPolicy:
    name: str
    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float


RETRY_POLICIES: dict[str, RetryPolicy] = {
    "market_public_read": RetryPolicy(
        name="market_public_read",
        max_attempts=3,
        base_delay_seconds=0.5,
        max_delay_seconds=2.0,
    ),
    "balance_account_read": RetryPolicy(
        name="balance_account_read",
        max_attempts=2,
        base_delay_seconds=0.75,
        max_delay_seconds=2.5,
    ),
    "open_order_status_read": RetryPolicy(
        name="open_order_status_read",
        max_attempts=2,
        base_delay_seconds=0.75,
        max_delay_seconds=2.5,
    ),
    "create_order": RetryPolicy(
        name="create_order",
        max_attempts=1,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
    ),
    "cancel_order": RetryPolicy(
        name="cancel_order",
        max_attempts=1,
        base_delay_seconds=0.0,
        max_delay_seconds=0.0,
    ),
    "notification_delivery": RetryPolicy(
        name="notification_delivery",
        max_attempts=3,
        base_delay_seconds=1.0,
        max_delay_seconds=3.0,
    ),
    "notification_poll": RetryPolicy(
        name="notification_poll",
        max_attempts=3,
        base_delay_seconds=1.0,
        max_delay_seconds=3.0,
    ),
}


def get_retry_policy(name: str) -> RetryPolicy:
    return RETRY_POLICIES.get(name, RETRY_POLICIES["open_order_status_read"])


def _message_contains_any(message: str, markers: tuple[str, ...]) -> bool:
    normalized = str(message or "").strip().lower()
    return any(marker in normalized for marker in markers)


def classify_retry_error(
    *,
    error: BaseException | None = None,
    status_code: int | None = None,
    error_message: str | None = None,
    response_text: str | None = None,
) -> dict[str, Any]:
    message = " ".join(
        [
            str(error_message or "").strip(),
            str(response_text or "").strip(),
            str(error or "").strip(),
        ]
    ).strip()
    normalized = message.lower()

    if isinstance(error, requests.Timeout) or _message_contains_any(
        normalized,
        ("timeout", "timed out", "read timed out"),
    ):
        return {
            "category": "timeout",
            "retryable": True,
            "ambiguous": True,
            "reason": message or "request timed out",
        }

    if isinstance(error, requests.ConnectionError) or _message_contains_any(
        normalized,
        (
            "connection",
            "connection aborted",
            "connection reset",
            "connection refused",
            "dns",
            "name resolution",
            "network",
            "temporarily unavailable",
        ),
    ):
        return {
            "category": "network",
            "retryable": True,
            "ambiguous": True,
            "reason": message or "network error",
        }

    if status_code == 429 or _message_contains_any(
        normalized,
        ("rate limit", "too many requests", "http 429"),
    ):
        return {
            "category": "rate_limit",
            "retryable": True,
            "ambiguous": True,
            "reason": message or "rate limit",
        }

    if (
        status_code is not None
        and 500 <= int(status_code) <= 599
    ) or _message_contains_any(
        normalized,
        (
            "bad gateway",
            "gateway timeout",
            "service unavailable",
            "internal server error",
            "upstream",
            "invalid json",
        ),
    ):
        return {
            "category": "server",
            "retryable": True,
            "ambiguous": True,
            "reason": message or "server error",
        }

    if (
        status_code in {401, 403}
        or _message_contains_any(
            normalized,
            (
                "unauthorized",
                "forbidden",
                "invalid signature",
                "api key",
                "api secret",
                "missing credentials",
                "permission denied",
                "authentication",
            ),
        )
    ):
        return {
            "category": "auth",
            "retryable": False,
            "ambiguous": False,
            "reason": message or "authentication error",
        }

    if (
        status_code in {400, 409, 422}
        or _message_contains_any(
            normalized,
            (
                "required",
                "invalid",
                "unsupported",
                "insufficient",
                "below requested",
                "not enough",
                "duplicate",
                "order not found",
                "no order",
            ),
        )
    ):
        return {
            "category": "validation",
            "retryable": False,
            "ambiguous": False,
            "reason": message or "validation error",
        }

    if status_code is not None and 400 <= int(status_code) <= 499:
        return {
            "category": "client",
            "retryable": False,
            "ambiguous": False,
            "reason": message or f"client error {status_code}",
        }

    if message:
        return {
            "category": "client",
            "retryable": False,
            "ambiguous": False,
            "reason": message,
        }

    return {
        "category": "network",
        "retryable": True,
        "ambiguous": True,
        "reason": "unknown request error",
    }


def should_retry(
    *,
    policy_name: str,
    classification: dict[str, Any],
    attempt: int,
) -> bool:
    policy = get_retry_policy(policy_name)
    if int(attempt) >= int(policy.max_attempts):
        return False

    if policy_name in {"create_order", "cancel_order"}:
        return False

    return bool(classification.get("retryable"))


def retry_delay_seconds(*, policy_name: str, attempt: int) -> float:
    policy = get_retry_policy(policy_name)
    if policy.max_attempts <= 1:
        return 0.0

    exponent = max(0, int(attempt) - 1)
    delay = float(policy.base_delay_seconds) * math.pow(2.0, exponent)
    return min(float(policy.max_delay_seconds), delay)


def log_api_retry_event(
    *,
    endpoint: str,
    action: str,
    attempt: int,
    policy_name: str,
    classification: dict[str, Any],
    outcome: str,
    correlation_id: str | None = None,
    delay_seconds: float | None = None,
    status_code: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    details = {
        "endpoint": endpoint,
        "action": action,
        "attempt": int(attempt),
        "policy": policy_name,
        "category": classification.get("category"),
        "retryable": bool(classification.get("retryable")),
        "ambiguous": bool(classification.get("ambiguous")),
        "reason": classification.get("reason"),
        "outcome": outcome,
        "correlation_id": correlation_id,
        "delay_seconds": delay_seconds,
        "status_code": status_code,
    }
    if metadata:
        details["metadata"] = metadata

    severity = "warning" if outcome in {"retrying", "blocked_ambiguous", "give_up"} else "info"
    try:
        insert_runtime_event(
            created_at=now_text(),
            event_type="api_retry",
            severity=severity,
            message=f"{action} {endpoint} {outcome}",
            details=details,
        )
    except Exception:
        return
