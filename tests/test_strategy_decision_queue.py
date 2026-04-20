from __future__ import annotations

import unittest

from ui.streamlit import pages


def _make_row(recommended_action: str, symbol: str = "THB_BTC") -> dict[str, object]:
    return {
        "symbol": symbol,
        "recommended_action": recommended_action,
        "freshness_status": "Fresh",
        "best_candidate": "FASTER_EXIT",
        "compare_verdict": "Clearly better",
        "action_reason": "Test reason",
        "last_candle_used": "2026-04-20 00:00:00",
    }


class StrategyDecisionQueueTests(unittest.TestCase):
    def test_grouped_queue_renders_expected_sections(self) -> None:
        rows = [
            _make_row("Promote", "THB_BTC"),
            _make_row("Keep", "THB_ETH"),
            _make_row("Prune candidate", "THB_TRX"),
            _make_row("Sync first", "THB_ADA"),
        ]

        groups = pages._group_strategy_decision_queue(rows)

        self.assertIn("Sync first", groups)
        self.assertIn("Promote", groups)
        self.assertIn("Prune candidate", groups)
        self.assertIn("Keep", groups)

    def test_stale_symbols_appear_in_sync_first(self) -> None:
        rows = [
            _make_row("Sync first", "THB_TRX"),
            _make_row("Sync first", "THB_ADA"),
            _make_row("Keep", "THB_ETH"),
        ]

        groups = pages._group_strategy_decision_queue(rows)

        sync_symbols = [r["symbol"] for r in groups["Sync first"]]
        self.assertIn("THB_TRX", sync_symbols)
        self.assertIn("THB_ADA", sync_symbols)
        self.assertEqual(len(groups["Keep"]), 1)

    def test_promote_ready_symbols_appear_in_promote(self) -> None:
        rows = [
            _make_row("Promote", "THB_BTC"),
            _make_row("Promote", "THB_SUMX"),
            _make_row("Keep", "THB_ETH"),
        ]

        groups = pages._group_strategy_decision_queue(rows)

        promote_symbols = [r["symbol"] for r in groups["Promote"]]
        self.assertIn("THB_BTC", promote_symbols)
        self.assertIn("THB_SUMX", promote_symbols)
        self.assertEqual(len(groups["Keep"]), 1)

    def test_prune_candidates_appear_in_prune_group(self) -> None:
        rows = [
            _make_row("Prune candidate", "THB_TRX"),
            _make_row("Keep", "THB_ETH"),
        ]

        groups = pages._group_strategy_decision_queue(rows)

        prune_symbols = [r["symbol"] for r in groups["Prune candidate"]]
        self.assertIn("THB_TRX", prune_symbols)
        self.assertEqual(len(prune_symbols), 1)

    def test_empty_rows_produce_empty_groups(self) -> None:
        groups = pages._group_strategy_decision_queue([])

        self.assertEqual(groups["Sync first"], [])
        self.assertEqual(groups["Promote"], [])
        self.assertEqual(groups["Prune candidate"], [])
        self.assertEqual(groups["Keep"], [])

    def test_unknown_action_is_not_included_in_any_group(self) -> None:
        rows = [_make_row("Unknown", "THB_BTC")]

        groups = pages._group_strategy_decision_queue(rows)

        total = sum(len(g) for g in groups.values())
        self.assertEqual(total, 0)

    def test_group_order_is_priority_ordered(self) -> None:
        groups = pages._group_strategy_decision_queue([])

        keys = list(groups.keys())
        self.assertEqual(keys[0], "Sync first")
        self.assertEqual(keys[1], "Promote")
        self.assertEqual(keys[2], "Prune candidate")
        self.assertEqual(keys[3], "Keep")

    def test_batch_navigation_does_not_mutate_input_rows(self) -> None:
        rows = [
            _make_row("Promote", "THB_BTC"),
            _make_row("Sync first", "THB_ETH"),
        ]
        original_len = len(rows)

        groups = pages._group_strategy_decision_queue(rows)

        self.assertEqual(len(rows), original_len)
        self.assertEqual(len(groups["Promote"]), 1)
        self.assertEqual(len(groups["Sync first"]), 1)


if __name__ == "__main__":
    unittest.main()
