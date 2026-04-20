from __future__ import annotations

import unittest

from ui.streamlit import pages


def _make_row(
    recommended_action: str,
    symbol: str = "THB_BTC",
    *,
    strength: str = "STRONG_KEEP",
    tuning_recommendation: str = "KEEP",
    best_pnl: float = 16.0,
    baseline_pnl: float = 12.0,
    freshness_status: str = "Fresh",
    blocking_warning: str = "NO",
    compare_verdict: str = "Clearly better",
    best_candidate: str = "FASTER_EXIT",
    in_live_rules: bool = False,
) -> dict[str, object]:
    edge = best_pnl - baseline_pnl
    return {
        "symbol": symbol,
        "recommended_action": recommended_action,
        "freshness_status": freshness_status,
        "last_candle_used": "2026-04-20 00:00:00",
        "in_live_rules": "YES" if in_live_rules else "NO",
        "best_candidate": best_candidate,
        "compare_verdict": compare_verdict,
        "strength": strength,
        "action_reason": "Test reason.",
        "tuning_recommendation": tuning_recommendation,
        "best_pnl_thb": best_pnl,
        "baseline_pnl_thb": baseline_pnl,
        "edge_vs_baseline_thb": edge,
        "blocking_warning": blocking_warning,
    }


class ActionPreviewBuildTests(unittest.TestCase):
    def test_promote_preview_has_expected_fields(self) -> None:
        row = _make_row("Promote", "THB_BTC")

        preview = pages._build_strategy_action_preview(row)

        self.assertEqual(preview["type"], "single")
        self.assertEqual(preview["symbol"], "THB_BTC")
        self.assertEqual(preview["recommended_action"], "Promote")
        self.assertEqual(preview["freshness"], "Fresh")
        self.assertEqual(preview["best_candidate"], "FASTER_EXIT")
        self.assertEqual(preview["compare_verdict"], "Clearly better")
        self.assertIn("reason", preview)
        self.assertIn("last_candle_used", preview)

    def test_promote_has_no_prune_grade(self) -> None:
        preview = pages._build_strategy_action_preview(_make_row("Promote"))

        self.assertIsNone(preview["prune_grade"])

    def test_promote_suggested_page_is_compare(self) -> None:
        preview = pages._build_strategy_action_preview(_make_row("Promote"))

        self.assertEqual(preview["suggested_next_page"], "Compare")

    def test_prune_preview_shows_grade(self) -> None:
        row = _make_row(
            "Prune candidate",
            strength="HIGH_PRUNE",
            tuning_recommendation="PRUNE",
            best_pnl=-6.0,
            baseline_pnl=-4.0,
            compare_verdict="Worse",
        )

        preview = pages._build_strategy_action_preview(row)

        self.assertIsNotNone(preview["prune_grade"])
        self.assertIn(preview["prune_grade"], {"Strong prune", "Review prune"})

    def test_prune_suggested_page_is_live_tuning(self) -> None:
        preview = pages._build_strategy_action_preview(
            _make_row("Prune candidate", strength="HIGH_PRUNE", tuning_recommendation="PRUNE")
        )

        self.assertEqual(preview["suggested_next_page"], "Live Tuning")

    def test_sync_first_preview_has_no_prune_grade(self) -> None:
        row = _make_row("Sync first", freshness_status="Stale")

        preview = pages._build_strategy_action_preview(row)

        self.assertIsNone(preview["prune_grade"])
        self.assertEqual(preview["suggested_next_page"], "Sync & Rank")

    def test_keep_suggested_page_is_compare(self) -> None:
        preview = pages._build_strategy_action_preview(_make_row("Keep"))

        self.assertEqual(preview["suggested_next_page"], "Compare")

    def test_preview_carries_pnl_fields(self) -> None:
        row = _make_row("Promote", best_pnl=20.0, baseline_pnl=10.0)

        preview = pages._build_strategy_action_preview(row)

        self.assertAlmostEqual(preview["best_pnl_thb"], 20.0)
        self.assertAlmostEqual(preview["baseline_pnl_thb"], 10.0)
        self.assertAlmostEqual(preview["edge_vs_baseline_thb"], 10.0)


class PruneStrengthClassificationTests(unittest.TestCase):
    def test_high_prune_and_tuning_prune_is_strong(self) -> None:
        row = _make_row(
            "Prune candidate",
            strength="HIGH_PRUNE",
            tuning_recommendation="PRUNE",
        )

        self.assertEqual(pages._classify_prune_strength(row), "Strong prune")

    def test_both_pnl_negative_and_tuning_prune_is_strong(self) -> None:
        row = _make_row(
            "Prune candidate",
            strength="REVIEW_SOON",
            tuning_recommendation="PRUNE",
            best_pnl=-3.0,
            baseline_pnl=-2.0,
        )

        self.assertEqual(pages._classify_prune_strength(row), "Strong prune")

    def test_single_signal_only_is_review(self) -> None:
        row = _make_row(
            "Prune candidate",
            strength="REVIEW_SOON",
            tuning_recommendation="PRUNE",
            best_pnl=2.0,
            baseline_pnl=1.0,
            blocking_warning="NO",
        )

        self.assertEqual(pages._classify_prune_strength(row), "Review prune")

    def test_blocking_warning_plus_negative_pnl_is_strong(self) -> None:
        row = _make_row(
            "Prune candidate",
            strength="REVIEW_SOON",
            tuning_recommendation="KEEP",
            best_pnl=-1.0,
            baseline_pnl=-0.5,
            blocking_warning="YES",
        )

        self.assertEqual(pages._classify_prune_strength(row), "Strong prune")

    def test_no_signals_is_review(self) -> None:
        row = _make_row(
            "Prune candidate",
            strength="BORDERLINE_KEEP",
            tuning_recommendation="KEEP",
            best_pnl=5.0,
            baseline_pnl=4.0,
            blocking_warning="NO",
        )

        self.assertEqual(pages._classify_prune_strength(row), "Review prune")


class BatchSyncPreviewTests(unittest.TestCase):
    def _make_sync_row(self, symbol: str, freshness_status: str) -> dict[str, object]:
        return _make_row("Sync first", symbol, freshness_status=freshness_status)

    def test_batch_sync_preview_has_symbol_count(self) -> None:
        rows = [
            self._make_sync_row("THB_BTC", "Stale"),
            self._make_sync_row("THB_ETH", "Missing"),
        ]

        preview = pages._build_strategy_batch_sync_preview(rows, resolution="60", days=7)

        self.assertEqual(preview["type"], "batch_sync")
        self.assertEqual(preview["symbol_count"], 2)

    def test_batch_sync_preview_lists_symbols(self) -> None:
        rows = [
            self._make_sync_row("THB_BTC", "Stale"),
            self._make_sync_row("THB_ETH", "Missing"),
        ]

        preview = pages._build_strategy_batch_sync_preview(rows, resolution="60", days=7)

        self.assertIn("THB_BTC", preview["symbols"])
        self.assertIn("THB_ETH", preview["symbols"])

    def test_batch_sync_preview_has_sync_reason(self) -> None:
        rows = [self._make_sync_row("THB_BTC", "Stale")]

        preview = pages._build_strategy_batch_sync_preview(rows, resolution="60", days=7)

        self.assertTrue(str(preview["sync_reason"]).strip())

    def test_batch_sync_preview_carries_resolution_and_days(self) -> None:
        rows = [self._make_sync_row("THB_BTC", "Stale")]

        preview = pages._build_strategy_batch_sync_preview(rows, resolution="240", days=14)

        self.assertEqual(preview["resolution"], "240")
        self.assertEqual(preview["days"], 14)

    def test_batch_sync_preview_suggested_page_is_sync_rank(self) -> None:
        preview = pages._build_strategy_batch_sync_preview(
            [self._make_sync_row("THB_BTC", "Stale")], resolution="60", days=7
        )

        self.assertEqual(preview["suggested_next_page"], "Sync & Rank")

    def test_batch_sync_preview_counts_stale_and_missing_separately(self) -> None:
        rows = [
            self._make_sync_row("THB_BTC", "Stale"),
            self._make_sync_row("THB_ETH", "Missing"),
            self._make_sync_row("THB_TRX", "Stale"),
        ]

        preview = pages._build_strategy_batch_sync_preview(rows, resolution="60", days=7)

        counts = preview["freshness_counts"]
        self.assertEqual(counts.get("Stale", 0), 2)
        self.assertEqual(counts.get("Missing", 0), 1)

    def test_empty_sync_rows_produces_zero_count(self) -> None:
        preview = pages._build_strategy_batch_sync_preview([], resolution="60", days=7)

        self.assertEqual(preview["symbol_count"], 0)
        self.assertEqual(preview["symbols"], [])


if __name__ == "__main__":
    unittest.main()
