from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from services import strategy_proposal_service as sps
from services.strategy_proposal_service import (
    ProposalTier,
    RuleProposal,
    assess_prune_readiness,
    build_prune_proposal,
    build_rule_update_proposal,
    classify_prune_tier,
    classify_rule_update_tier,
    compute_prune_confidence,
    compute_rule_update_confidence,
    group_proposals_by_tier,
    summarize_proposal_counts,
)


def _op_state(
    *,
    open_buy: int = 0,
    open_sell: int = 0,
    reserved_thb: float = 0.0,
    reserved_coin: float = 0.0,
    partial_fill: bool = False,
    review_reasons: list[str] | None = None,
) -> dict:
    return {
        "open_buy_count": open_buy,
        "open_sell_count": open_sell,
        "reserved_thb": reserved_thb,
        "reserved_coin": reserved_coin,
        "partial_fill": partial_fill,
        "review_reasons": list(review_reasons or []),
    }


def _compare_row(
    variant: str,
    *,
    total_pnl: float = 0.0,
    trades: int = 0,
    win_rate: float = 0.0,
    fee_guardrail: str = "FEE_OK",
    rule: dict | None = None,
) -> dict:
    return {
        "variant": variant,
        "total_pnl_thb": total_pnl,
        "trades": trades,
        "win_rate_percent": win_rate,
        "fee_guardrail": fee_guardrail,
        "rule": dict(
            rule
            or {
                "buy_below": 100.0,
                "sell_above": 110.0,
                "budget_thb": 500.0,
                "stop_loss_percent": 2.0,
                "take_profit_percent": 3.0,
                "max_trades_per_day": 4,
            }
        ),
    }


class AssessPruneReadinessTests(unittest.TestCase):
    def test_zero_linked_state_with_soft_warning_is_prune_ready(self) -> None:
        assessment = assess_prune_readiness(
            _op_state(review_reasons=["exchange open-orders coverage is partial"])
        )

        self.assertEqual(assessment["hard_block_reasons"], [])
        self.assertIn(
            "exchange open-orders coverage is partial",
            assessment["soft_warning_reasons"],
        )
        self.assertFalse(assessment["has_real_open_orders"])
        self.assertFalse(assessment["has_ghost_reserved"])

    def test_ghost_reserved_without_open_orders_is_not_blocked(self) -> None:
        assessment = assess_prune_readiness(
            _op_state(
                reserved_thb=250.0,
                review_reasons=["exchange open-orders coverage is partial"],
            )
        )

        self.assertEqual(assessment["hard_block_reasons"], [])
        self.assertTrue(assessment["has_ghost_reserved"])
        self.assertFalse(assessment["has_real_open_orders"])
        ghost_warning = next(
            (w for w in assessment["soft_warning_reasons"] if "reserved balance detected" in w),
            "",
        )
        self.assertTrue(ghost_warning, assessment["soft_warning_reasons"])

    def test_real_open_orders_with_soft_warning_escalate_to_hard_block(self) -> None:
        assessment = assess_prune_readiness(
            _op_state(
                open_buy=1,
                review_reasons=["exchange open-orders coverage is partial"],
            )
        )

        self.assertIn(
            "exchange open-orders coverage is partial",
            assessment["hard_block_reasons"],
        )
        self.assertEqual(assessment["soft_warning_reasons"], [])
        self.assertTrue(assessment["has_real_open_orders"])

    def test_partial_fill_is_always_hard_block(self) -> None:
        assessment = assess_prune_readiness(_op_state(partial_fill=True))

        self.assertIn("partial fill is still unresolved", assessment["hard_block_reasons"])

    def test_hard_review_reason_always_hard_block(self) -> None:
        assessment = assess_prune_readiness(
            _op_state(review_reasons=["missing on exchange needs review"])
        )

        self.assertIn("missing on exchange needs review", assessment["hard_block_reasons"])

    def test_ghost_reserved_only_no_soft_warning_still_yields_warning(self) -> None:
        assessment = assess_prune_readiness(_op_state(reserved_coin=0.5))

        self.assertEqual(assessment["hard_block_reasons"], [])
        self.assertTrue(assessment["has_ghost_reserved"])
        self.assertTrue(
            any("reserved balance detected" in w for w in assessment["soft_warning_reasons"])
        )


class ConfidenceScoringTests(unittest.TestCase):
    def test_strong_positive_signal_scores_high(self) -> None:
        best = _compare_row("FASTER_EXIT", total_pnl=180.0, trades=15, win_rate=70.0)
        baseline = _compare_row("CURRENT", total_pnl=40.0, trades=15, win_rate=60.0)

        confidence = compute_rule_update_confidence(
            best_row=best, baseline_row=baseline, freshness_status="Fresh"
        )

        self.assertGreaterEqual(confidence, 0.80)

    def test_stale_freshness_drags_confidence_down(self) -> None:
        best = _compare_row("FASTER_EXIT", total_pnl=40.0, trades=6, win_rate=55.0)
        baseline = _compare_row("CURRENT", total_pnl=30.0, trades=6, win_rate=55.0)

        fresh = compute_rule_update_confidence(
            best_row=best, baseline_row=baseline, freshness_status="Fresh"
        )
        stale = compute_rule_update_confidence(
            best_row=best, baseline_row=baseline, freshness_status="Stale"
        )

        self.assertLess(stale, fresh)
        self.assertAlmostEqual(fresh - stale, 0.10, places=2)

    def test_negative_edge_reduces_confidence(self) -> None:
        best = _compare_row("FASTER_EXIT", total_pnl=-30.0, trades=4, win_rate=30.0)
        baseline = _compare_row("CURRENT", total_pnl=10.0, trades=4, win_rate=50.0)

        confidence = compute_rule_update_confidence(
            best_row=best, baseline_row=baseline, freshness_status="Fresh"
        )

        self.assertLess(confidence, 0.40)

    def test_fee_guardrail_loss_after_fees_reduces_confidence_vs_fee_ok(self) -> None:
        baseline = _compare_row("CURRENT", total_pnl=20.0, trades=3, win_rate=50.0)
        fee_ok = _compare_row(
            "FASTER_EXIT",
            total_pnl=40.0,
            trades=3,
            win_rate=50.0,
            fee_guardrail="FEE_OK",
        )
        fee_loss = _compare_row(
            "FASTER_EXIT",
            total_pnl=40.0,
            trades=3,
            win_rate=50.0,
            fee_guardrail="LOSS_AFTER_FEES",
        )

        ok_conf = compute_rule_update_confidence(
            best_row=fee_ok, baseline_row=baseline, freshness_status="Fresh"
        )
        loss_conf = compute_rule_update_confidence(
            best_row=fee_loss, baseline_row=baseline, freshness_status="Fresh"
        )

        self.assertGreater(ok_conf - loss_conf, 0.40)


class ClassifyRuleUpdateTierTests(unittest.TestCase):
    def test_hard_blocks_force_blocked(self) -> None:
        tier = classify_rule_update_tier(
            confidence=0.95,
            best_pnl_thb=200.0,
            edge_thb=100.0,
            fee_guardrail="FEE_OK",
            freshness_status="Fresh",
            hard_blocks=["no candle data"],
            has_warnings=False,
        )
        self.assertEqual(tier, ProposalTier.BLOCKED)

    def test_high_confidence_no_warnings_is_auto_approve(self) -> None:
        tier = classify_rule_update_tier(
            confidence=0.90,
            best_pnl_thb=150.0,
            edge_thb=80.0,
            fee_guardrail="FEE_OK",
            freshness_status="Fresh",
            hard_blocks=[],
            has_warnings=False,
        )
        self.assertEqual(tier, ProposalTier.AUTO_APPROVE)

    def test_high_confidence_with_warning_drops_to_recommended(self) -> None:
        tier = classify_rule_update_tier(
            confidence=0.85,
            best_pnl_thb=150.0,
            edge_thb=80.0,
            fee_guardrail="FEE_OK",
            freshness_status="Fresh",
            hard_blocks=[],
            has_warnings=True,
        )
        self.assertEqual(tier, ProposalTier.RECOMMENDED)

    def test_negative_pnl_is_needs_review(self) -> None:
        tier = classify_rule_update_tier(
            confidence=0.85,
            best_pnl_thb=-10.0,
            edge_thb=5.0,
            fee_guardrail="FEE_OK",
            freshness_status="Fresh",
            hard_blocks=[],
            has_warnings=False,
        )
        self.assertEqual(tier, ProposalTier.NEEDS_REVIEW)

    def test_loss_after_fees_is_needs_review(self) -> None:
        tier = classify_rule_update_tier(
            confidence=0.90,
            best_pnl_thb=150.0,
            edge_thb=80.0,
            fee_guardrail="LOSS_AFTER_FEES",
            freshness_status="Fresh",
            hard_blocks=[],
            has_warnings=False,
        )
        self.assertEqual(tier, ProposalTier.NEEDS_REVIEW)

    def test_stale_freshness_is_needs_review(self) -> None:
        tier = classify_rule_update_tier(
            confidence=0.90,
            best_pnl_thb=150.0,
            edge_thb=80.0,
            fee_guardrail="FEE_OK",
            freshness_status="Stale",
            hard_blocks=[],
            has_warnings=False,
        )
        self.assertEqual(tier, ProposalTier.NEEDS_REVIEW)


class PruneConfidenceAndTierTests(unittest.TestCase):
    def test_high_prune_signal_exceeds_auto_threshold(self) -> None:
        confidence = compute_prune_confidence(
            tuning_row={"recommendation": "PRUNE", "confidence": "HIGH_PRUNE"},
            baseline_pnl_thb=-12.0,
            best_pnl_thb=-5.0,
            fee_guardrail="LOSS_AFTER_FEES",
        )
        self.assertGreaterEqual(confidence, 0.70)
        tier = classify_prune_tier(confidence=confidence, hard_blocks=[])
        self.assertEqual(tier, ProposalTier.AUTO_APPROVE)

    def test_hard_blocks_force_blocked_tier(self) -> None:
        tier = classify_prune_tier(
            confidence=0.95, hard_blocks=["partial fill is still unresolved"]
        )
        self.assertEqual(tier, ProposalTier.BLOCKED)

    def test_weak_signal_is_needs_review(self) -> None:
        confidence = compute_prune_confidence(
            tuning_row={},
            baseline_pnl_thb=5.0,
            best_pnl_thb=8.0,
            fee_guardrail="FEE_OK",
        )
        tier = classify_prune_tier(confidence=confidence, hard_blocks=[])
        self.assertEqual(tier, ProposalTier.NEEDS_REVIEW)


class BuildRuleUpdateProposalTests(unittest.TestCase):
    def test_empty_compare_rows_returns_none(self) -> None:
        self.assertIsNone(
            build_rule_update_proposal(
                symbol="THB_XYZ",
                current_rule={},
                compare_rows=[],
                freshness_status="Fresh",
            )
        )

    def test_auto_approve_on_strong_uplift(self) -> None:
        rows = [
            _compare_row("CURRENT", total_pnl=30.0, trades=12, win_rate=60.0),
            _compare_row(
                "FASTER_EXIT",
                total_pnl=200.0,
                trades=18,
                win_rate=72.0,
                fee_guardrail="FEE_OK",
            ),
        ]
        proposal = build_rule_update_proposal(
            symbol="THB_BTC",
            current_rule={"buy_below": 100.0},
            compare_rows=rows,
            freshness_status="Fresh",
        )
        assert proposal is not None
        self.assertEqual(proposal.symbol, "THB_BTC")
        self.assertEqual(proposal.tier, ProposalTier.AUTO_APPROVE)
        self.assertEqual(proposal.best_variant, "FASTER_EXIT")
        self.assertAlmostEqual(proposal.edge_thb, 170.0, places=2)
        self.assertEqual(proposal.hard_blocks, [])
        self.assertTrue(proposal.expires_at)

    def test_missing_candles_produces_blocked_tier_with_hard_block(self) -> None:
        rows = [
            _compare_row("CURRENT", total_pnl=0.0),
            _compare_row("FASTER_EXIT", total_pnl=0.0),
        ]
        proposal = build_rule_update_proposal(
            symbol="THB_XYZ",
            current_rule={},
            compare_rows=rows,
            freshness_status="Missing",
        )
        assert proposal is not None
        self.assertEqual(proposal.tier, ProposalTier.BLOCKED)
        self.assertTrue(proposal.hard_blocks)

    def test_proposal_is_expired_after_ttl_elapses(self) -> None:
        rows = [
            _compare_row("CURRENT", total_pnl=10.0),
            _compare_row("FASTER_EXIT", total_pnl=80.0, trades=10, win_rate=60.0),
        ]
        proposal = build_rule_update_proposal(
            symbol="THB_BTC",
            current_rule={},
            compare_rows=rows,
            freshness_status="Fresh",
            ttl_seconds=60,
        )
        assert proposal is not None
        future = datetime.now(timezone.utc) + timedelta(seconds=120)
        self.assertTrue(proposal.is_expired(now=future))
        self.assertFalse(proposal.is_expired(now=datetime.now(timezone.utc)))


class BuildPruneProposalTests(unittest.TestCase):
    def test_false_blocked_regression_soft_warning_zero_orders(self) -> None:
        proposal = build_prune_proposal(
            symbol="THB_FF",
            operational_state=_op_state(
                review_reasons=["exchange open-orders coverage is partial"]
            ),
            tuning_row={"recommendation": "PRUNE", "confidence": "HIGH_PRUNE"},
            baseline_pnl_thb=-8.0,
            best_pnl_thb=-3.0,
            fee_guardrail="LOSS_AFTER_FEES",
        )

        self.assertFalse(proposal.is_blocked)
        self.assertEqual(proposal.hard_blocks, [])
        self.assertIn(
            "exchange open-orders coverage is partial",
            proposal.warnings,
        )
        self.assertEqual(proposal.tier, ProposalTier.AUTO_APPROVE)

    def test_real_open_order_with_soft_warning_is_blocked(self) -> None:
        proposal = build_prune_proposal(
            symbol="THB_FF",
            operational_state=_op_state(
                open_buy=1,
                review_reasons=["exchange open-orders coverage is partial"],
            ),
            tuning_row={"recommendation": "PRUNE"},
        )

        self.assertTrue(proposal.is_blocked)
        self.assertEqual(proposal.tier, ProposalTier.BLOCKED)

    def test_ghost_reserved_without_open_orders_is_warning_only(self) -> None:
        proposal = build_prune_proposal(
            symbol="THB_FF",
            operational_state=_op_state(reserved_thb=125.0),
            tuning_row={"recommendation": "PRUNE", "confidence": "HIGH_PRUNE"},
            baseline_pnl_thb=-20.0,
            best_pnl_thb=-5.0,
            fee_guardrail="LOSS_AFTER_FEES",
        )

        self.assertFalse(proposal.is_blocked)
        self.assertTrue(proposal.has_ghost_reserved)
        self.assertTrue(
            any("reserved balance detected" in w for w in proposal.warnings)
        )


class GroupingTests(unittest.TestCase):
    def test_group_and_summarize(self) -> None:
        proposals = [
            RuleProposal(
                symbol="A",
                tier=ProposalTier.AUTO_APPROVE,
                confidence=0.9,
                current_rule={},
                proposed_rule={},
                reason="ok",
            ),
            RuleProposal(
                symbol="B",
                tier=ProposalTier.RECOMMENDED,
                confidence=0.7,
                current_rule={},
                proposed_rule={},
                reason="ok",
            ),
            RuleProposal(
                symbol="C",
                tier=ProposalTier.AUTO_APPROVE,
                confidence=0.85,
                current_rule={},
                proposed_rule={},
                reason="ok",
            ),
        ]
        buckets = group_proposals_by_tier(proposals)
        counts = summarize_proposal_counts(proposals)

        self.assertEqual([p.symbol for p in buckets[ProposalTier.AUTO_APPROVE.value]], ["A", "C"])
        self.assertEqual([p.symbol for p in buckets[ProposalTier.RECOMMENDED.value]], ["B"])
        self.assertEqual(counts[ProposalTier.AUTO_APPROVE.value], 2)
        self.assertEqual(counts[ProposalTier.RECOMMENDED.value], 1)
        self.assertEqual(counts[ProposalTier.NEEDS_REVIEW.value], 0)
        self.assertEqual(counts[ProposalTier.BLOCKED.value], 0)


class ModuleContractTests(unittest.TestCase):
    def test_soft_review_reasons_is_immutable_frozenset(self) -> None:
        self.assertIsInstance(sps.SOFT_REVIEW_REASONS, frozenset)
        self.assertIn("exchange open-orders coverage is partial", sps.SOFT_REVIEW_REASONS)


if __name__ == "__main__":
    unittest.main()
