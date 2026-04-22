from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

_TEST_TEMP_DIR = Path.cwd() / ".tmp" / "streamlit_tests"
_TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_TEST_TEMP_DIR)
os.environ["TEMP"] = str(_TEST_TEMP_DIR)
os.environ["TMPDIR"] = str(_TEST_TEMP_DIR)
tempfile.tempdir = str(_TEST_TEMP_DIR)

from services.strategy_proposal_service import ProposalTier
from ui.streamlit import strategy_inbox


def _config() -> dict[str, Any]:
    return {
        "mode": "paper",
        "rules": {
            "THB_AAA": {
                "buy_below": 10.0,
                "sell_above": 11.0,
                "budget_thb": 200.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 2,
            },
            "THB_BBB": {
                "buy_below": 5.0,
                "sell_above": 5.5,
                "budget_thb": 150.0,
                "stop_loss_percent": 1.2,
                "take_profit_percent": 1.8,
                "max_trades_per_day": 2,
            },
        },
        "watchlist_symbols": ["THB_AAA", "THB_BBB"],
        "fee_rate": 0.0025,
        "cooldown_seconds": 60,
        "live_auto_entry_min_score": 50.0,
        "live_auto_entry_allowed_biases": ["bullish", "mixed"],
    }


def _tuning_row(
    symbol: str,
    *,
    recommendation: str = "KEEP",
    confidence: str = "REVIEW",
    replay_pnl_thb: float = 50.0,
    fee_guardrail: str = "FEE_OK",
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "recommendation": recommendation,
        "confidence": confidence,
        "replay_pnl_thb": replay_pnl_thb,
        "fee_guardrail": fee_guardrail,
    }


def _compare_row(
    variant: str,
    *,
    total_pnl: float,
    trades: int = 10,
    win_rate: float = 60.0,
    fee_guardrail: str = "FEE_OK",
    bars: int = 120,
    rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "variant": variant,
        "total_pnl_thb": total_pnl,
        "trades": trades,
        "win_rate_percent": win_rate,
        "fee_guardrail": fee_guardrail,
        "bars": bars,
        "rule": dict(
            rule
            or {
                "buy_below": 10.0,
                "sell_above": 11.0,
                "budget_thb": 200.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 2,
            }
        ),
    }


class _StubOpState:
    @staticmethod
    def context(**_: Any) -> dict[str, Any]:
        return {"stub": True}

    @staticmethod
    def state(*, symbol: str, **_: Any) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "open_buy_count": 0,
            "open_sell_count": 0,
            "reserved_thb": 0.0,
            "reserved_coin": 0.0,
            "partial_fill": False,
            "review_reasons": [],
        }


class RecomputeProposalsTests(unittest.TestCase):
    def test_prune_flagged_symbols_skip_compare(self) -> None:
        compare_calls: list[str] = []

        def compare_runner(**kwargs: Any) -> list[dict[str, Any]]:
            compare_calls.append(str(kwargs.get("symbol")))
            return [
                _compare_row("CURRENT", total_pnl=20.0),
                _compare_row("FASTER_EXIT", total_pnl=80.0),
            ]

        def tuning_builder(**_: Any) -> list[dict[str, Any]]:
            return [
                _tuning_row(
                    "THB_AAA",
                    recommendation="PRUNE",
                    confidence="HIGH_PRUNE",
                    replay_pnl_thb=-12.0,
                    fee_guardrail="LOSS_AFTER_FEES",
                ),
                _tuning_row(
                    "THB_BBB",
                    recommendation="KEEP",
                    replay_pnl_thb=30.0,
                ),
            ]

        result = strategy_inbox.recompute_proposals(
            config=_config(),
            private_ctx={},
            runtime={},
            latest_prices={"THB_AAA": 10.0, "THB_BBB": 5.0},
            ranking_builder=lambda **_: {"rows": []},
            tuning_builder=tuning_builder,
            compare_runner=compare_runner,
            op_context_builder=_StubOpState.context,
            op_state_builder=_StubOpState.state,
        )

        self.assertEqual(compare_calls, ["THB_BBB"])
        self.assertEqual({p.symbol for p in result["prunes"]}, {"THB_AAA"})
        self.assertEqual({u.symbol for u in result["updates"]}, {"THB_BBB"})
        prune_proposal = result["prunes"][0]
        self.assertEqual(prune_proposal.tier, ProposalTier.AUTO_APPROVE)
        self.assertFalse(prune_proposal.is_blocked)

    def test_confident_uplift_becomes_auto_approve_update(self) -> None:
        def compare_runner(**_: Any) -> list[dict[str, Any]]:
            return [
                _compare_row("CURRENT", total_pnl=40.0, trades=12, win_rate=60.0),
                _compare_row(
                    "FASTER_EXIT",
                    total_pnl=220.0,
                    trades=18,
                    win_rate=72.0,
                    bars=240,
                ),
            ]

        def tuning_builder(**_: Any) -> list[dict[str, Any]]:
            return [_tuning_row("THB_AAA", replay_pnl_thb=40.0)]

        config = _config()
        config["rules"].pop("THB_BBB")
        config["watchlist_symbols"] = ["THB_AAA"]

        result = strategy_inbox.recompute_proposals(
            config=config,
            private_ctx={},
            runtime={},
            latest_prices={"THB_AAA": 10.0},
            ranking_builder=lambda **_: {"rows": []},
            tuning_builder=tuning_builder,
            compare_runner=compare_runner,
            op_context_builder=_StubOpState.context,
            op_state_builder=_StubOpState.state,
        )

        self.assertEqual(len(result["updates"]), 1)
        proposal = result["updates"][0]
        self.assertEqual(proposal.symbol, "THB_AAA")
        self.assertEqual(proposal.tier, ProposalTier.AUTO_APPROVE)
        self.assertEqual(proposal.best_variant, "FASTER_EXIT")
        self.assertAlmostEqual(proposal.edge_thb, 180.0, places=2)
        self.assertEqual(proposal.hard_blocks, [])

    def test_compare_failure_goes_to_skipped(self) -> None:
        def compare_runner(**_: Any) -> list[dict[str, Any]]:
            raise RuntimeError("replay unavailable")

        def tuning_builder(**_: Any) -> list[dict[str, Any]]:
            return [_tuning_row("THB_AAA", replay_pnl_thb=30.0)]

        config = _config()
        config["rules"].pop("THB_BBB")

        result = strategy_inbox.recompute_proposals(
            config=config,
            private_ctx={},
            runtime={},
            latest_prices={"THB_AAA": 10.0},
            ranking_builder=lambda **_: {"rows": []},
            tuning_builder=tuning_builder,
            compare_runner=compare_runner,
            op_context_builder=_StubOpState.context,
            op_state_builder=_StubOpState.state,
        )

        self.assertEqual(result["updates"], [])
        self.assertEqual(result["prunes"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["symbol"], "THB_AAA")
        self.assertIn("compare error", result["skipped"][0]["reason"])

    def test_ghost_reserved_prune_is_warning_only(self) -> None:
        def op_state(*, symbol: str, **_: Any) -> dict[str, Any]:
            return {
                "symbol": symbol,
                "open_buy_count": 0,
                "open_sell_count": 0,
                "reserved_thb": 120.0,
                "reserved_coin": 0.0,
                "partial_fill": False,
                "review_reasons": ["exchange open-orders coverage is partial"],
            }

        def tuning_builder(**_: Any) -> list[dict[str, Any]]:
            return [
                _tuning_row(
                    "THB_AAA",
                    recommendation="PRUNE",
                    confidence="HIGH_PRUNE",
                    replay_pnl_thb=-20.0,
                    fee_guardrail="LOSS_AFTER_FEES",
                )
            ]

        config = _config()
        config["rules"].pop("THB_BBB")

        result = strategy_inbox.recompute_proposals(
            config=config,
            private_ctx={},
            runtime={},
            latest_prices={"THB_AAA": 10.0},
            ranking_builder=lambda **_: {"rows": []},
            tuning_builder=tuning_builder,
            compare_runner=lambda **_: [],
            op_context_builder=_StubOpState.context,
            op_state_builder=op_state,
        )

        self.assertEqual(len(result["prunes"]), 1)
        proposal = result["prunes"][0]
        self.assertFalse(proposal.is_blocked)
        self.assertTrue(proposal.has_ghost_reserved)
        self.assertIn(
            "exchange open-orders coverage is partial",
            proposal.warnings,
        )

    def test_real_open_order_prune_is_blocked(self) -> None:
        def op_state(*, symbol: str, **_: Any) -> dict[str, Any]:
            return {
                "symbol": symbol,
                "open_buy_count": 1,
                "open_sell_count": 0,
                "reserved_thb": 100.0,
                "reserved_coin": 0.0,
                "partial_fill": False,
                "review_reasons": ["exchange open-orders coverage is partial"],
            }

        def tuning_builder(**_: Any) -> list[dict[str, Any]]:
            return [
                _tuning_row(
                    "THB_AAA",
                    recommendation="PRUNE",
                    confidence="HIGH_PRUNE",
                    replay_pnl_thb=-10.0,
                )
            ]

        config = _config()
        config["rules"].pop("THB_BBB")

        result = strategy_inbox.recompute_proposals(
            config=config,
            private_ctx={},
            runtime={},
            latest_prices={"THB_AAA": 10.0},
            ranking_builder=lambda **_: {"rows": []},
            tuning_builder=tuning_builder,
            compare_runner=lambda **_: [],
            op_context_builder=_StubOpState.context,
            op_state_builder=op_state,
        )

        proposal = result["prunes"][0]
        self.assertTrue(proposal.is_blocked)
        self.assertEqual(proposal.tier, ProposalTier.BLOCKED)

    def test_snapshot_metadata_is_populated(self) -> None:
        def tuning_builder(**_: Any) -> list[dict[str, Any]]:
            return []

        result = strategy_inbox.recompute_proposals(
            config={"rules": {}, "watchlist_symbols": []},
            ranking_builder=lambda **_: {"rows": []},
            tuning_builder=tuning_builder,
            compare_runner=lambda **_: [],
            op_context_builder=_StubOpState.context,
            op_state_builder=_StubOpState.state,
            resolution="60",
            lookback_days=7,
        )

        self.assertEqual(result["resolution"], "60")
        self.assertEqual(result["lookback_days"], 7)
        self.assertTrue(result["snapshot_ts"])
        self.assertEqual(result["updates"], [])
        self.assertEqual(result["prunes"], [])


class FreshnessFromCompareRowsTests(unittest.TestCase):
    def test_no_rows_is_missing(self) -> None:
        self.assertEqual(strategy_inbox._freshness_from_compare_rows([]), "Missing")

    def test_zero_bars_is_missing(self) -> None:
        self.assertEqual(
            strategy_inbox._freshness_from_compare_rows([{"bars": 0}]),
            "Missing",
        )

    def test_few_bars_is_stale(self) -> None:
        self.assertEqual(
            strategy_inbox._freshness_from_compare_rows([{"bars": 5}]),
            "Stale",
        )

    def test_many_bars_is_fresh(self) -> None:
        self.assertEqual(
            strategy_inbox._freshness_from_compare_rows([{"bars": 120}]),
            "Fresh",
        )


class TuningRowIsPruneTests(unittest.TestCase):
    def test_recommendation_prune_returns_true(self) -> None:
        self.assertTrue(
            strategy_inbox._tuning_row_is_prune({"recommendation": "PRUNE"})
        )

    def test_high_prune_confidence_returns_true(self) -> None:
        self.assertTrue(
            strategy_inbox._tuning_row_is_prune(
                {"recommendation": "REVIEW", "confidence": "HIGH_PRUNE"}
            )
        )

    def test_keep_returns_false(self) -> None:
        self.assertFalse(
            strategy_inbox._tuning_row_is_prune({"recommendation": "KEEP"})
        )

    def test_empty_row_returns_false(self) -> None:
        self.assertFalse(strategy_inbox._tuning_row_is_prune({}))


if __name__ == "__main__":
    unittest.main()
