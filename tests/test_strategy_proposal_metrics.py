from __future__ import annotations

import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import db_service, strategy_proposal_ledger as ledger
from services.strategy_proposal_metrics import (
    DEFAULT_WINDOW_HOURS,
    compute_ledger_summary,
)
from services.strategy_proposal_service import (
    ProposalKind,
    ProposalTier,
    PruneProposal,
    RuleProposal,
)


def _rule(
    *,
    symbol: str = "BTC_THB",
    snapshot_ts: str = "2026-04-22T10:00:00+00:00",
    expires_at: str = "2026-04-22T10:05:00+00:00",
    tier: ProposalTier = ProposalTier.AUTO_APPROVE,
    proposed_rule: dict | None = None,
) -> RuleProposal:
    return RuleProposal(
        symbol=symbol,
        tier=tier,
        confidence=0.9,
        current_rule={"buy_below": -1.0, "sell_above": 1.0},
        proposed_rule=proposed_rule or {"buy_below": -1.2, "sell_above": 1.4},
        reason="metrics test",
        warnings=[],
        hard_blocks=[],
        best_variant="TEST",
        baseline_pnl_thb=0.0,
        proposed_pnl_thb=10.0,
        edge_thb=10.0,
        win_rate_percent=60.0,
        trades=12,
        fee_guardrail="FEE_OK",
        freshness_status="Fresh",
        snapshot_ts=snapshot_ts,
        expires_at=expires_at,
    )


def _prune(
    *,
    symbol: str = "DOGE_THB",
    snapshot_ts: str = "2026-04-22T10:00:00+00:00",
) -> PruneProposal:
    return PruneProposal(
        symbol=symbol,
        tier=ProposalTier.AUTO_APPROVE,
        confidence=0.8,
        reason="prune",
        snapshot_ts=snapshot_ts,
    )


class MetricsTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        safe_name = self.id().replace(".", "_")
        self._temp_dir = Path.cwd() / "data" / "test_strategy_proposal_metrics" / safe_name
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)


class LedgerSummaryTests(MetricsTestBase):
    def test_empty_ledger_returns_zero_counts(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        summary = compute_ledger_summary(now=now)

        self.assertEqual(summary.counts_by_status["pending"], 0)
        self.assertEqual(summary.counts_by_status["applied"], 0)
        self.assertEqual(summary.counts_by_kind[ProposalKind.RULE_UPDATE.value], 0)
        self.assertEqual(summary.apply_rate, 0.0)
        self.assertEqual(summary.dismissal_rate, 0.0)
        self.assertIsNone(summary.avg_time_to_decision_seconds)
        self.assertEqual(summary.recent_decisions, [])
        self.assertEqual(summary.window_hours, DEFAULT_WINDOW_HOURS)

    def test_counts_reflect_upserted_proposals(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_rule(), _prune()], now=now)

        summary = compute_ledger_summary(now=now)
        self.assertEqual(summary.counts_by_status["pending"], 2)
        self.assertEqual(summary.counts_by_kind[ProposalKind.RULE_UPDATE.value], 1)
        self.assertEqual(summary.counts_by_kind[ProposalKind.PRUNE.value], 1)
        self.assertEqual(summary.counts_by_tier[ProposalTier.AUTO_APPROVE.value], 2)

    def test_apply_rate_counts_applied_over_decisions_in_window(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending(
            [
                _rule(symbol="A_THB", proposed_rule={"buy_below": -1.2, "sell_above": 1.4}),
                _rule(symbol="B_THB", proposed_rule={"buy_below": -1.3, "sell_above": 1.5}),
                _rule(symbol="C_THB", proposed_rule={"buy_below": -1.4, "sell_above": 1.6}),
            ],
            now=now,
        )
        active = ledger.list_active(now=now)
        self.assertEqual(len(active), 3)

        ledger.mark_applied(active[0].proposal_id, actor_id="alice", now=now)
        ledger.mark_dismissed(active[1].proposal_id, actor_id="alice", now=now)

        summary = compute_ledger_summary(now=now)
        self.assertEqual(summary.window_counts_by_decision["applied"], 1)
        self.assertEqual(summary.window_counts_by_decision["dismissed"], 1)
        self.assertAlmostEqual(summary.apply_rate, 0.5, places=4)
        self.assertAlmostEqual(summary.dismissal_rate, 0.5, places=4)

    def test_avg_time_to_decision_is_positive_after_delay(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_rule()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        later = now + timedelta(minutes=3)
        ledger.mark_applied(pid, actor_id="alice", now=later)

        summary = compute_ledger_summary(now=later)
        self.assertIsNotNone(summary.avg_time_to_decision_seconds)
        self.assertAlmostEqual(
            summary.avg_time_to_decision_seconds, 180.0, delta=2.0
        )

    def test_recent_decisions_joins_proposal_symbol_and_kind(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_rule(symbol="BTC_THB")], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        ledger.mark_dismissed(pid, actor_id="alice", now=now, reason="noisy")

        summary = compute_ledger_summary(now=now)
        latest = summary.recent_decisions[0]
        self.assertEqual(latest["decision"], "dismissed")
        self.assertEqual(latest["symbol"], "BTC_THB")
        self.assertEqual(latest["kind"], ProposalKind.RULE_UPDATE.value)
        self.assertEqual(latest["actor_id"], "alice")

    def test_window_excludes_old_decisions(self) -> None:
        long_ago = datetime(2026, 4, 20, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_rule()], now=long_ago)
        pid = ledger.list_active(now=long_ago)[0].proposal_id
        ledger.mark_applied(pid, actor_id="alice", now=long_ago)

        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        summary = compute_ledger_summary(window_hours=24, now=now)
        self.assertEqual(summary.window_counts_by_decision["applied"], 0)
        # But lifetime counts still reflect the applied row.
        self.assertEqual(summary.counts_by_status["applied"], 1)


if __name__ == "__main__":
    unittest.main()
