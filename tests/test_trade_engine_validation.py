from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from core import trade_engine


def _rule() -> dict:
    return {
        "buy_below": 9.0,
        "sell_above": 11.0,
        "budget_thb": 100.0,
        "stop_loss_percent": 2.0,
        "take_profit_percent": 5.0,
        "max_trades_per_day": 3,
    }


class TradeEngineValidationTests(unittest.TestCase):
    def test_create_position_rejects_non_positive_last_price(self) -> None:
        with patch.object(trade_engine, "get_fee_rate", return_value=0.0025):
            with self.assertRaises(ValueError):
                trade_engine.create_position_from_coin_qty(
                    last_price=0.0,
                    coin_qty=1.0,
                    config=_rule(),
                    timestamp="2026-04-24 00:00:00",
                    entry_source="strategy_buy",
                )
            with self.assertRaises(ValueError):
                trade_engine.create_position_from_coin_qty(
                    last_price=-1.0,
                    coin_qty=1.0,
                    config=_rule(),
                    timestamp="2026-04-24 00:00:00",
                    entry_source="strategy_buy",
                )

    def test_create_position_rejects_non_positive_coin_qty(self) -> None:
        with patch.object(trade_engine, "get_fee_rate", return_value=0.0025):
            with self.assertRaises(ValueError):
                trade_engine.create_position_from_coin_qty(
                    last_price=10.0,
                    coin_qty=0.0,
                    config=_rule(),
                    timestamp="2026-04-24 00:00:00",
                    entry_source="strategy_buy",
                )

    def test_open_position_skips_when_last_price_is_invalid(self) -> None:
        positions: dict = {}
        buffer = io.StringIO()
        with patch.object(trade_engine, "get_fee_rate", return_value=0.0025):
            with redirect_stdout(buffer):
                trade_engine.open_position(
                    "THB_BTC",
                    0.0,
                    _rule(),
                    positions,
                    "2026-04-24 00:00:00",
                )
        self.assertEqual(positions, {})
        self.assertIn("invalid last_price", buffer.getvalue())

    def test_open_position_creates_position_on_valid_inputs(self) -> None:
        positions: dict = {}
        with (
            patch.object(trade_engine, "get_fee_rate", return_value=0.0025),
            patch.object(trade_engine, "beep_alert"),
            redirect_stdout(io.StringIO()),
        ):
            trade_engine.open_position(
                "THB_BTC",
                10.0,
                _rule(),
                positions,
                "2026-04-24 00:00:00",
            )
        self.assertIn("THB_BTC", positions)
        self.assertGreater(positions["THB_BTC"]["coin_qty"], 0)
        self.assertGreater(positions["THB_BTC"]["budget_thb"], 0)

    def test_handle_symbol_skips_when_last_price_is_invalid(self) -> None:
        positions: dict = {}
        daily_stats: dict = {}
        cooldowns: dict = {}
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            trade_engine.handle_symbol(
                symbol="THB_BTC",
                zone="BUY",
                zone_changed_flag=True,
                last_price=0.0,
                config=_rule(),
                positions=positions,
                daily_stats=daily_stats,
                cooldowns=cooldowns,
                timestamp="2026-04-24 00:00:00",
            )
        self.assertEqual(positions, {})
        self.assertIn("invalid last_price", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
