from __future__ import annotations

import unittest

from ui.streamlit import pages


def _freshness(status: str, *, last_timestamp: str = "2026-04-20 00:00:00") -> dict[str, object]:
    return {
        "status": status,
        "last_timestamp_text": last_timestamp if status != "Missing" else "Missing",
        "warning": (
            "Sync fresh data before running Compare or applying a variant."
            if status in {"Missing", "Stale"}
            else None
        ),
    }


def _compare_rows(
    *,
    baseline_pnl: float,
    candidate_variant: str,
    candidate_decision: str,
    candidate_pnl: float,
    candidate_fee_guardrail: str = "FEE_OK",
) -> list[dict[str, object]]:
    return [
        {
            "variant": "CURRENT",
            "decision": "Current baseline",
            "total_pnl_thb": float(baseline_pnl),
            "fee_guardrail": "FEE_OK",
        },
        {
            "variant": candidate_variant,
            "decision": candidate_decision,
            "total_pnl_thb": float(candidate_pnl),
            "fee_guardrail": candidate_fee_guardrail,
        },
    ]


class StrategyDecisionSummaryTests(unittest.TestCase):
    def test_stale_data_is_classified_as_sync_first(self) -> None:
        row = pages._classify_strategy_decision_row(
            symbol="THB_TRX",
            in_live_rules=True,
            freshness=_freshness("Stale", last_timestamp="2026-04-18 00:00:00"),
            compare_rows=[],
            tuning_row={"recommendation": "KEEP", "confidence": "STRONG_KEEP"},
        )

        self.assertEqual(row["recommended_action"], "Sync first")

    def test_clearly_better_fresh_non_live_symbol_is_promote(self) -> None:
        row = pages._classify_strategy_decision_row(
            symbol="THB_SUMX",
            in_live_rules=False,
            freshness=_freshness("Fresh"),
            compare_rows=_compare_rows(
                baseline_pnl=12.0,
                candidate_variant="FASTER_EXIT",
                candidate_decision="Clearly better",
                candidate_pnl=16.0,
            ),
            tuning_row=None,
        )

        self.assertEqual(row["recommended_action"], "Promote")
        self.assertEqual(row["best_candidate"], "FASTER_EXIT")

    def test_tied_baseline_like_symbol_is_keep(self) -> None:
        row = pages._classify_strategy_decision_row(
            symbol="THB_FF",
            in_live_rules=True,
            freshness=_freshness("Fresh"),
            compare_rows=_compare_rows(
                baseline_pnl=9.0,
                candidate_variant="WIDER_TAKE_PROFIT",
                candidate_decision="Tied with baseline",
                candidate_pnl=9.4,
            ),
            tuning_row={"recommendation": "KEEP", "confidence": "BORDERLINE_KEEP"},
        )

        self.assertEqual(row["recommended_action"], "Keep")

    def test_clearly_poor_live_symbol_is_prune_candidate(self) -> None:
        row = pages._classify_strategy_decision_row(
            symbol="THB_TRX",
            in_live_rules=True,
            freshness=_freshness("Fresh"),
            compare_rows=_compare_rows(
                baseline_pnl=-4.0,
                candidate_variant="FASTER_EXIT",
                candidate_decision="Worse",
                candidate_pnl=-6.0,
                candidate_fee_guardrail="LOSS_AFTER_FEES",
            ),
            tuning_row={"recommendation": "PRUNE", "confidence": "HIGH_PRUNE"},
        )

        self.assertEqual(row["recommended_action"], "Prune candidate")

    def test_grouped_counts_follow_classified_actions(self) -> None:
        rows = [
            {"recommended_action": "Promote"},
            {"recommended_action": "Keep"},
            {"recommended_action": "Prune candidate"},
            {"recommended_action": "Sync first"},
            {"recommended_action": "Sync first"},
        ]

        counts = pages._summarize_strategy_decision_counts(rows)

        self.assertEqual(
            counts,
            {
                "Promote": 1,
                "Keep": 1,
                "Prune candidate": 1,
                "Sync first": 2,
            },
        )


if __name__ == "__main__":
    unittest.main()
