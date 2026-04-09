from __future__ import annotations

import unittest

from clients.bitkub_private_client import BitkubPrivateClientError
from services.execution_service import (
    ORDER_STATE_CANCELED,
    ORDER_STATE_FILLED,
    ORDER_STATE_OPEN,
    refresh_live_order_from_exchange,
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


class _FakeClient:
    def __init__(
        self,
        *,
        order_info_result=None,
        order_info_error: Exception | None = None,
        open_orders_result=None,
        open_orders_error: Exception | None = None,
        order_history_result=None,
        order_history_error: Exception | None = None,
    ) -> None:
        self.order_info_result = order_info_result
        self.order_info_error = order_info_error
        self.open_orders_result = open_orders_result
        self.open_orders_error = open_orders_error
        self.order_history_result = order_history_result
        self.order_history_error = order_history_error

    def get_order_info(self, *, order_id, symbol=None, side=None):
        if self.order_info_error is not None:
            raise self.order_info_error
        return self.order_info_result

    def get_open_orders(self, symbol=None):
        if self.open_orders_error is not None:
            raise self.open_orders_error
        return self.open_orders_result

    def get_order_history(self, *, symbol=None, page=None, limit=None):
        if self.order_history_error is not None:
            raise self.order_history_error
        return self.order_history_result


class ExecutionOrderRefreshTests(unittest.TestCase):
    def test_refresh_uses_order_history_fallback_when_order_info_fails(self) -> None:
        client = _FakeClient(
            order_info_error=BitkubPrivateClientError("Bitkub API error=24 message=order not found"),
            open_orders_result={"result": []},
            order_history_result={
                "result": [
                    {"id": "abc123", "status": "cancelled", "partial_filled": False}
                ]
            },
        )

        refreshed_record, events = refresh_live_order_from_exchange(
            client=client,
            order_record=_open_order_record(),
            occurred_at="2026-04-09 12:05:00",
        )

        self.assertEqual(refreshed_record["state"], ORDER_STATE_CANCELED)
        self.assertEqual(events[0]["event_type"], "order_history_refresh")

    def test_refresh_marks_canceled_when_order_disappears_from_exchange_probes(self) -> None:
        client = _FakeClient(
            order_info_error=BitkubPrivateClientError("Bitkub API error=24 message=order not found"),
            open_orders_result={"result": []},
            order_history_result={"result": []},
        )

        refreshed_record, events = refresh_live_order_from_exchange(
            client=client,
            order_record=_open_order_record(),
            occurred_at="2026-04-09 12:05:00",
        )

        self.assertEqual(refreshed_record["state"], ORDER_STATE_CANCELED)
        self.assertEqual(events[0]["event_type"], "exchange_absence_refresh")

    def test_refresh_uses_open_orders_fallback_when_order_info_fails(self) -> None:
        client = _FakeClient(
            order_info_error=BitkubPrivateClientError("Bitkub API error=24 message=order not found"),
            open_orders_result={
                "result": [
                    {"id": "abc123", "status": "unfilled", "partial_filled": False}
                ]
            },
            order_history_result={"result": []},
        )

        refreshed_record, events = refresh_live_order_from_exchange(
            client=client,
            order_record=_open_order_record(),
            occurred_at="2026-04-09 12:05:00",
        )

        self.assertEqual(refreshed_record["state"], ORDER_STATE_OPEN)
        self.assertEqual(events[0]["event_type"], "open_orders_refresh")

    def test_refresh_maps_filled_order_from_history(self) -> None:
        client = _FakeClient(
            order_info_error=BitkubPrivateClientError("Bitkub API error=24 message=order not found"),
            open_orders_result={"result": []},
            order_history_result={
                "result": [
                    {"id": "abc123", "status": "filled", "filled": 10, "total": 10}
                ]
            },
        )

        refreshed_record, events = refresh_live_order_from_exchange(
            client=client,
            order_record=_open_order_record(),
            occurred_at="2026-04-09 12:05:00",
        )

        self.assertEqual(refreshed_record["state"], ORDER_STATE_FILLED)
        self.assertEqual(events[0]["event_type"], "order_history_refresh")


if __name__ == "__main__":
    unittest.main()
