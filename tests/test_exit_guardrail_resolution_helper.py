from __future__ import annotations

import unittest

from services.execution_service import build_exit_guardrail_resolution


class ExitGuardrailResolutionHelperTests(unittest.TestCase):
    def test_clamps_suggested_rate_to_upper_band_when_request_is_too_high(self) -> None:
        resolution = build_exit_guardrail_resolution(
            symbol="THB_TRX",
            requested_sell_rate=10.5,
            latest_price=10.0,
            live_slippage_tolerance_percent=1.0,
            quote_observed_at="2026-04-19 10:00:00",
            quote_checked_at="2026-04-19 10:00:20",
            quote_stale_after_seconds=30.0,
        )

        self.assertEqual(resolution["quote_freshness"], "fresh")
        self.assertAlmostEqual(float(resolution["allowed_sell_band_low"]), 9.9, places=8)
        self.assertAlmostEqual(float(resolution["allowed_sell_band_high"]), 10.1, places=8)
        self.assertAlmostEqual(float(resolution["deviation_percent"]), 5.0, places=8)
        self.assertAlmostEqual(float(resolution["suggested_safe_sell_rate"]), 10.1, places=8)
        self.assertEqual(resolution["suggestion_reason"], "clamped_to_upper_band")

    def test_stale_quote_disables_safe_rate_suggestion(self) -> None:
        resolution = build_exit_guardrail_resolution(
            symbol="THB_TRX",
            requested_sell_rate=10.5,
            latest_price=10.0,
            live_slippage_tolerance_percent=1.0,
            quote_observed_at="2026-04-19 10:00:00",
            quote_checked_at="2026-04-19 10:01:10",
            quote_stale_after_seconds=30.0,
        )

        self.assertEqual(resolution["quote_freshness"], "stale")
        self.assertIsNone(resolution["suggested_safe_sell_rate"])
        self.assertFalse(bool(resolution["quote_safe_for_suggestion"]))
        self.assertEqual(resolution["suggestion_reason"], "quote_stale")

    def test_unavailable_quote_returns_no_band_and_no_suggestion(self) -> None:
        resolution = build_exit_guardrail_resolution(
            symbol="THB_TRX",
            requested_sell_rate=10.5,
            latest_price=0.0,
            live_slippage_tolerance_percent=1.0,
            quote_observed_at="2026-04-19 10:00:00",
            quote_checked_at="2026-04-19 10:00:10",
        )

        self.assertEqual(resolution["quote_freshness"], "unavailable")
        self.assertIsNone(resolution["latest_live_price"])
        self.assertIsNone(resolution["allowed_sell_band_low"])
        self.assertIsNone(resolution["allowed_sell_band_high"])
        self.assertIsNone(resolution["deviation_percent"])
        self.assertIsNone(resolution["suggested_safe_sell_rate"])
        self.assertFalse(bool(resolution["quote_safe_for_suggestion"]))


if __name__ == "__main__":
    unittest.main()
