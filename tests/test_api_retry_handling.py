from __future__ import annotations

import unittest

import requests

from clients.bitkub_private_client import BitkubPrivateClientError
from services.api_retry_service import classify_retry_error, should_retry
from services.execution_service import (
    ORDER_STATE_CANCELED,
    LiveExecutionGuardrailError,
    cancel_live_order,
    submit_auto_live_entry_order,
)


def _open_order_record() -> dict[str, object]:
    return {
        "created_at": "2026-04-09 12:00:00",
        "updated_at": "2026-04-09 12:00:00",
        "symbol": "THB_TRX",
        "side": "sell",
        "order_type": "limit",
        "state": "open",
        "request_payload": {"sym": "THB_TRX"},
        "response_payload": None,
        "guardrails": {},
        "exchange_order_id": "abc123",
        "exchange_client_id": None,
        "message": "live order refreshed from Bitkub order_info",
    }


class RetryClassificationTests(unittest.TestCase):
    def test_retry_classification_maps_common_failure_classes(self) -> None:
        timeout = classify_retry_error(error=requests.Timeout("timed out"))
        self.assertEqual(timeout["category"], "timeout")
        self.assertTrue(timeout["retryable"])

        rate_limit = classify_retry_error(status_code=429, response_text="too many requests")
        self.assertEqual(rate_limit["category"], "rate_limit")
        self.assertTrue(rate_limit["retryable"])

        auth = classify_retry_error(error_message="missing credentials")
        self.assertEqual(auth["category"], "auth")
        self.assertFalse(auth["retryable"])

        validation = classify_retry_error(
            error_message="Bitkub API error=10 message=sym is required"
        )
        self.assertEqual(validation["category"], "validation")
        self.assertFalse(validation["retryable"])

        self.assertFalse(
            should_retry(
                policy_name="create_order",
                classification=timeout,
                attempt=1,
            )
        )
        self.assertTrue(
            should_retry(
                policy_name="market_public_read",
                classification=timeout,
                attempt=1,
            )
        )


class _AmbiguousCreateClient:
    def __init__(self) -> None:
        self.place_bid_calls = 0
        self.open_orders_calls = 0
        self.order_history_calls = 0

    def place_bid(self, payload, *, correlation_id=None):
        self.place_bid_calls += 1
        raise BitkubPrivateClientError(
            "connection reset by peer",
            category="network",
            retryable=True,
            ambiguous=True,
        )

    def get_open_orders(self, symbol=None):
        self.open_orders_calls += 1
        return {"result": []}

    def get_order_history(self, *, symbol=None, page=None, limit=None):
        self.order_history_calls += 1
        return {"result": []}


class _AmbiguousCancelClient:
    def __init__(self) -> None:
        self.cancel_calls = 0
        self.order_info_calls = 0

    def cancel_order(self, payload, *, correlation_id=None):
        self.cancel_calls += 1
        if self.cancel_calls == 1:
            raise BitkubPrivateClientError(
                "connection reset by peer",
                category="network",
                retryable=True,
                ambiguous=True,
            )
        return {"result": {"id": "abc123"}}

    def get_order_info(self, *, order_id, symbol=None, side=None):
        self.order_info_calls += 1
        if self.order_info_calls == 1:
            return {"result": {"id": "abc123", "status": "unfilled", "partial_filled": False}}
        return {"result": {"id": "abc123", "status": "cancelled", "partial_filled": False}}

    def get_open_orders(self, symbol=None):
        return {"result": []}

    def get_order_history(self, *, symbol=None, page=None, limit=None):
        return {"result": []}


class EndpointSafetyTests(unittest.TestCase):
    def test_ambiguous_create_requires_reconciliation_before_retry(self) -> None:
        client = _AmbiguousCreateClient()

        with self.assertRaises(LiveExecutionGuardrailError):
            submit_auto_live_entry_order(
                client=client,
                symbol="THB_BTC",
                amount_thb=100.0,
                rate=10.0,
                latest_price=10.0,
                signal_reason="BUY_ZONE_ENTRY",
                guardrails={
                    "blocked_reasons": [],
                    "live_max_order_thb": 1000.0,
                    "live_slippage_tolerance_percent": 2.0,
                },
                available_balances={"THB": 1000.0},
                created_at="2026-04-19T12:00:00+07:00",
                correlation_id="corr-create",
            )

        self.assertEqual(client.place_bid_calls, 1)
        self.assertEqual(client.open_orders_calls, 1)
        self.assertEqual(client.order_history_calls, 1)

    def test_ambiguous_cancel_rechecks_status_before_retry(self) -> None:
        client = _AmbiguousCancelClient()

        updated_record, events = cancel_live_order(
            client=client,
            order_record=_open_order_record(),
            occurred_at="2026-04-19T12:00:00+07:00",
            correlation_id="corr-cancel",
        )

        self.assertEqual(updated_record["state"], ORDER_STATE_CANCELED)
        self.assertEqual(client.cancel_calls, 2)
        self.assertEqual(client.order_info_calls, 2)
        self.assertEqual(events[0]["event_type"], "cancel_status_recheck")
        self.assertEqual(events[1]["event_type"], "order_info_refresh")
        self.assertEqual(events[2]["event_type"], "cancel_request_retry")
        self.assertEqual(events[3]["event_type"], "order_info_refresh")


if __name__ == "__main__":
    unittest.main()
