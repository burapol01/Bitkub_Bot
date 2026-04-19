from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.streamlit import symbol_state


class SymbolStateTests(unittest.TestCase):
    def test_build_symbol_operational_state_marks_linked_orders_and_review_required(self) -> None:
        account_snapshot = {
            "balances": {
                "ok": True,
                "data": {
                    "result": {
                        "THB": {"available": 5000.0, "reserved": 1000.0},
                        "BTC": {"available": 0.4, "reserved": 0.1},
                    }
                },
            },
            "open_orders_meta": {"mode": "global", "requires_symbol": False},
            "open_orders": {
                "THB_BTC": {"ok": True, "data": {"result": [{"id": "1", "side": "buy"}]}},
            },
        }
        open_execution_orders = [
            {
                "id": 1,
                "symbol": "THB_BTC",
                "side": "buy",
                "state": "open",
                "request_payload": {"amt": 1000.0},
            },
            {
                "id": 2,
                "symbol": "THB_BTC",
                "side": "sell",
                "state": "partially_filled",
                "request_payload": {"amt": 0.2},
            },
        ]

        with (
            patch.object(symbol_state, "fetch_open_execution_orders", return_value=open_execution_orders),
            patch.object(symbol_state, "fetch_latest_filled_execution_orders_by_symbol", return_value={}),
            patch.object(
                symbol_state,
                "fetch_recent_trade_journal",
                return_value=[
                    {
                        "created_at": "2026-04-19 12:00:00",
                        "channel": "auto_live_exit",
                        "status": "blocked",
                        "message": "sell guardrail blocked the exit",
                        "details": {"reason": "sell guardrail blocked the exit"},
                    }
                ],
            ),
        ):
            state = symbol_state.build_symbol_operational_state(
                symbol="THB_BTC",
                config={
                    "live_auto_entry_enabled": True,
                    "live_auto_exit_enabled": True,
                },
                account_snapshot=account_snapshot,
                latest_prices={"THB_BTC": 1000.0},
                runtime={"manual_pause": False, "safety_pause": False},
            )

        self.assertEqual(state["open_buy_count"], 1)
        self.assertEqual(state["open_sell_count"], 1)
        self.assertAlmostEqual(state["reserved_thb"], 1000.0)
        self.assertAlmostEqual(state["reserved_coin"], 0.1)
        self.assertTrue(state["partial_fill"])
        self.assertTrue(state["entry_blocked"])
        self.assertTrue(state["exit_blocked"])
        self.assertTrue(state["review_required"])
        self.assertIsNotNone(state["recent_guardrail_block"])


if __name__ == "__main__":
    unittest.main()
