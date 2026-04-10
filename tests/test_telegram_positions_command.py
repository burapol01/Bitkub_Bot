from __future__ import annotations

import unittest

from main import build_telegram_position_line


class TelegramPositionsCommandTests(unittest.TestCase):
    def test_build_telegram_position_line_uses_latest_price_and_rule_defaults(self) -> None:
        line = build_telegram_position_line(
            symbol="THB_BTC",
            latest_prices={"THB_BTC": 110.0},
            rules={
                "THB_BTC": {
                    "sell_above": 120.0,
                    "stop_loss_percent": 1.5,
                    "take_profit_percent": 3.0,
                }
            },
            fee_rate=0.0025,
            positions={
                "THB_BTC": {
                    "buy_price": 100.0,
                    "coin_qty": 1.0,
                    "budget_thb": 100.0,
                    "entry_source": "strategy_buy",
                }
            },
        )

        self.assertIn("THB_BTC:", line)
        self.assertIn("target=120.00000000", line)
        self.assertIn("sl=1.50%", line)
        self.assertIn("tp=3.00%", line)

    def test_build_telegram_position_line_handles_symbol_missing_from_rules(self) -> None:
        line = build_telegram_position_line(
            symbol="THB_ALT",
            latest_prices={},
            rules={},
            fee_rate=0.0025,
            positions={
                "THB_ALT": {
                    "buy_price": 5.0,
                    "coin_qty": 2.0,
                    "budget_thb": 10.0,
                    "sell_above": 5.5,
                    "stop_loss_percent": 1.0,
                    "take_profit_percent": 2.0,
                    "entry_source": "wallet_import",
                }
            },
        )

        self.assertIn("THB_ALT:", line)
        self.assertIn("target=5.50000000", line)
        self.assertIn("src=wallet", line)


if __name__ == "__main__":
    unittest.main()
