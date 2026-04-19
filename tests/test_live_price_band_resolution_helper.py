from __future__ import annotations

import unittest

from services.execution_service import build_live_price_band_resolution


class LivePriceBandResolutionHelperTests(unittest.TestCase):
    def test_buy_resolution_clamps_to_lower_band_when_request_is_too_low(self) -> None:
        resolution = build_live_price_band_resolution(
            symbol="THB_TRX",
            side="buy",
            requested_rate=9.5,
            latest_price=10.0,
            live_slippage_tolerance_percent=1.0,
            quote_observed_at="2026-04-19 10:00:00",
            quote_checked_at="2026-04-19 10:00:20",
            quote_stale_after_seconds=30.0,
        )

        self.assertEqual(resolution["side"], "buy")
        self.assertEqual(resolution["quote_freshness"], "fresh")
        self.assertAlmostEqual(float(resolution["allowed_band_low"]), 9.9, places=8)
        self.assertAlmostEqual(float(resolution["allowed_band_high"]), 10.1, places=8)
        self.assertAlmostEqual(float(resolution["suggested_safe_rate"]), 9.9, places=8)
        self.assertEqual(resolution["suggestion_reason"], "clamped_to_lower_band")

    def test_sell_resolution_clamps_to_upper_band_when_request_is_too_high(self) -> None:
        resolution = build_live_price_band_resolution(
            symbol="THB_TRX",
            side="sell",
            requested_rate=10.5,
            latest_price=10.0,
            live_slippage_tolerance_percent=1.0,
            quote_observed_at="2026-04-19 10:00:00",
            quote_checked_at="2026-04-19 10:00:20",
            quote_stale_after_seconds=30.0,
        )

        self.assertEqual(resolution["side"], "sell")
        self.assertAlmostEqual(float(resolution["suggested_safe_rate"]), 10.1, places=8)
        self.assertEqual(resolution["suggestion_reason"], "clamped_to_upper_band")

    def test_stale_quote_disables_suggestion(self) -> None:
        resolution = build_live_price_band_resolution(
            symbol="THB_TRX",
            side="buy",
            requested_rate=9.5,
            latest_price=10.0,
            live_slippage_tolerance_percent=1.0,
            quote_observed_at="2026-04-19 10:00:00",
            quote_checked_at="2026-04-19 10:01:10",
            quote_stale_after_seconds=30.0,
        )

        self.assertEqual(resolution["quote_freshness"], "stale")
        self.assertIsNone(resolution["suggested_safe_rate"])
        self.assertFalse(bool(resolution["quote_safe_for_suggestion"]))
        self.assertEqual(resolution["suggestion_reason"], "quote_stale")


if __name__ == "__main__":
    unittest.main()
