from __future__ import annotations

import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services import db_service, strategy_proposal_ledger as ledger
from services.strategy_proposal_service import (
    ProposalKind,
    ProposalTier,
    PruneProposal,
    RuleProposal,
    build_prune_proposal,
    build_rule_update_proposal,
    rule_hash,
    stable_proposal_id,
)


def _make_rule_proposal(
    *,
    symbol: str = "BTC_THB",
    proposed_rule: dict | None = None,
    expires_at: str | None = None,
    snapshot_ts: str | None = None,
    tier: ProposalTier = ProposalTier.AUTO_APPROVE,
    confidence: float = 0.9,
) -> RuleProposal:
    return RuleProposal(
        symbol=symbol,
        tier=tier,
        confidence=confidence,
        current_rule={"buy_below": -1.0, "sell_above": 1.0},
        proposed_rule=proposed_rule or {"buy_below": -1.2, "sell_above": 1.4},
        reason="test",
        warnings=[],
        hard_blocks=[],
        best_variant="TEST_VARIANT",
        baseline_pnl_thb=10.0,
        proposed_pnl_thb=20.0,
        edge_thb=10.0,
        win_rate_percent=60.0,
        trades=12,
        fee_guardrail="FEE_OK",
        freshness_status="Fresh",
        snapshot_ts=snapshot_ts or "2026-04-22T10:00:00+00:00",
        expires_at=expires_at or "2026-04-22T10:05:00+00:00",
    )


def _make_prune_proposal(
    *,
    symbol: str = "DOGE_THB",
    snapshot_ts: str | None = None,
    tier: ProposalTier = ProposalTier.AUTO_APPROVE,
    confidence: float = 0.8,
) -> PruneProposal:
    return PruneProposal(
        symbol=symbol,
        tier=tier,
        confidence=confidence,
        reason="live tuning flags PRUNE",
        warnings=[],
        hard_blocks=[],
        remove_from_watchlist=False,
        tuning_recommendation="PRUNE",
        baseline_pnl_thb=-5.0,
        best_pnl_thb=-5.0,
        snapshot_ts=snapshot_ts or "2026-04-22T10:00:00+00:00",
    )


class LedgerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        safe_name = self.id().replace(".", "_")
        self._temp_dir = Path.cwd() / "data" / "test_strategy_proposal_ledger" / safe_name
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)


class SchemaTests(LedgerTestBase):
    def test_tables_and_indexes_exist(self) -> None:
        with db_service._connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        self.assertIn("strategy_proposals", tables)
        self.assertIn("strategy_proposal_decisions", tables)
        for expected in (
            "idx_strategy_proposals_status_kind",
            "idx_strategy_proposals_symbol_kind",
            "idx_strategy_proposals_dismissed",
            "idx_strategy_proposals_expires_at",
            "idx_strategy_proposal_decisions_proposal",
        ):
            self.assertIn(expected, indexes, f"missing index: {expected}")

    def test_foreign_key_cascade(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        row = ledger.list_active(now=now)[0]
        with db_service._connect() as conn:
            conn.execute(
                "DELETE FROM strategy_proposals WHERE proposal_id = ?",
                (row.proposal_id,),
            )
            remaining = conn.execute(
                "SELECT COUNT(*) FROM strategy_proposal_decisions WHERE proposal_id = ?",
                (row.proposal_id,),
            ).fetchone()[0]
        self.assertEqual(remaining, 0)


class StableIdTests(unittest.TestCase):
    def test_stable_proposal_id_same_bucket_dedups(self) -> None:
        rhash = rule_hash({"a": 1})
        id1 = stable_proposal_id(
            symbol="BTC_THB",
            kind="RULE_UPDATE",
            rule_hash_value=rhash,
            snapshot_ts="2026-04-22T10:00:00+00:00",
            bucket_seconds=300,
        )
        id2 = stable_proposal_id(
            symbol="BTC_THB",
            kind="RULE_UPDATE",
            rule_hash_value=rhash,
            snapshot_ts="2026-04-22T10:04:00+00:00",
            bucket_seconds=300,
        )
        self.assertEqual(id1, id2)

    def test_stable_proposal_id_across_buckets_differs(self) -> None:
        rhash = rule_hash({"a": 1})
        id1 = stable_proposal_id(
            symbol="BTC_THB",
            kind="RULE_UPDATE",
            rule_hash_value=rhash,
            snapshot_ts="2026-04-22T10:00:00+00:00",
            bucket_seconds=300,
        )
        id2 = stable_proposal_id(
            symbol="BTC_THB",
            kind="RULE_UPDATE",
            rule_hash_value=rhash,
            snapshot_ts="2026-04-22T10:06:00+00:00",
            bucket_seconds=300,
        )
        self.assertNotEqual(id1, id2)

    def test_rule_hash_order_independent(self) -> None:
        self.assertEqual(
            rule_hash({"a": 1, "b": 2}),
            rule_hash({"b": 2, "a": 1}),
        )

    def test_rule_hash_detects_change(self) -> None:
        self.assertNotEqual(
            rule_hash({"a": 1}),
            rule_hash({"a": 2}),
        )


class UpsertTests(LedgerTestBase):
    def test_upsert_persists_new_proposal(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        proposal = _make_rule_proposal()
        result = ledger.upsert_pending([proposal], now=now)
        self.assertEqual(len(result.persisted), 1)
        self.assertEqual(result.deduped, [])

        active = ledger.list_active(now=now)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].status, ledger.ProposalStatus.PENDING)
        self.assertEqual(active[0].tier, ProposalTier.AUTO_APPROVE.value)

        decisions = ledger.list_decisions(active[0].proposal_id)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision"], "created")
        self.assertEqual(decisions[0]["actor_type"], "system")

    def test_same_bucket_upsert_dedups(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        second = ledger.upsert_pending([_make_rule_proposal()], now=now)
        self.assertEqual(second.persisted, [])
        self.assertEqual(len(second.deduped), 1)

        active = ledger.list_active(now=now)
        self.assertEqual(len(active), 1)
        decisions = ledger.list_decisions(active[0].proposal_id)
        self.assertEqual(len([d for d in decisions if d["decision"] == "created"]), 1)

    def test_new_rule_hash_supersedes_old_pending(self) -> None:
        now1 = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        now2 = datetime(2026, 4, 22, 10, 10, 0, tzinfo=timezone.utc)
        ledger.upsert_pending(
            [
                _make_rule_proposal(
                    proposed_rule={"buy_below": -1.2, "sell_above": 1.4},
                    snapshot_ts="2026-04-22T10:00:00+00:00",
                )
            ],
            now=now1,
        )
        result = ledger.upsert_pending(
            [
                _make_rule_proposal(
                    proposed_rule={"buy_below": -1.5, "sell_above": 1.8},
                    snapshot_ts="2026-04-22T10:10:00+00:00",
                    expires_at="2026-04-22T10:15:00+00:00",
                )
            ],
            now=now2,
        )
        self.assertEqual(len(result.persisted), 1)
        self.assertEqual(len(result.superseded), 1)

        active = ledger.list_active(now=now2)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].rule_hash, rule_hash({"buy_below": -1.5, "sell_above": 1.8}))

    def test_prune_proposal_persists_with_prune_kind(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_prune_proposal()], now=now)
        active = ledger.list_active(kind=ProposalKind.PRUNE.value, now=now)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].kind, ProposalKind.PRUNE.value)
        self.assertEqual(active[0].symbol, "DOGE_THB")


class LifecycleTests(LedgerTestBase):
    def test_mark_applied_transitions_pending_to_applied(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id

        applied = ledger.mark_applied(pid, actor_id="alice", metadata={"source": "ui"}, now=now)
        self.assertEqual(applied.status, ledger.ProposalStatus.APPLIED)

        self.assertEqual(ledger.list_active(now=now), [])
        decisions = ledger.list_decisions(pid)
        decisions_by_kind = {d["decision"] for d in decisions}
        self.assertIn("applied", decisions_by_kind)
        applied_decision = next(d for d in decisions if d["decision"] == "applied")
        self.assertEqual(applied_decision["actor_id"], "alice")
        self.assertEqual(applied_decision["metadata"].get("source"), "ui")

    def test_apply_rejects_terminal_state(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        ledger.mark_applied(pid, actor_id="alice", now=now)
        with self.assertRaises(ledger.LedgerError):
            ledger.mark_applied(pid, actor_id="alice", now=now)

    def test_apply_unknown_id_raises(self) -> None:
        with self.assertRaises(ledger.LedgerError):
            ledger.mark_applied("does-not-exist", actor_id="alice")

    def test_sweep_expired_marks_pending_expired(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        future = now + timedelta(minutes=10)
        expired = ledger.sweep_expired(now=future)
        self.assertEqual(len(expired), 1)

        self.assertEqual(ledger.list_active(now=future), [])
        stored = ledger.get(expired[0])
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, ledger.ProposalStatus.EXPIRED)

    def test_list_active_auto_sweeps_expired(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        future = now + timedelta(minutes=10)
        self.assertEqual(ledger.list_active(now=future), [])


class TTLEnforcementTests(LedgerTestBase):
    def test_apply_after_ttl_raises_and_marks_expired(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id

        too_late = now + timedelta(minutes=10)
        with self.assertRaises(ledger.LedgerError):
            ledger.mark_applied(pid, actor_id="alice", now=too_late)

        stored = ledger.get(pid)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, ledger.ProposalStatus.EXPIRED)
        decisions_by_kind = {d["decision"] for d in ledger.list_decisions(pid)}
        self.assertIn("expired", decisions_by_kind)
        self.assertNotIn("applied", decisions_by_kind)

    def test_apply_exactly_at_expiry_is_rejected(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        at_expiry = datetime(2026, 4, 22, 10, 5, 0, tzinfo=timezone.utc)
        with self.assertRaises(ledger.LedgerError):
            ledger.mark_applied(pid, actor_id="alice", now=at_expiry)

    def test_prune_without_expiry_is_not_marked_expired_by_sweep(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_prune_proposal()], now=now)
        future = now + timedelta(days=1)
        swept = ledger.sweep_expired(now=future)
        self.assertEqual(swept, [])
        active = ledger.list_active(kind=ProposalKind.PRUNE.value, now=future)
        self.assertEqual(len(active), 1)


class DismissalTests(LedgerTestBase):
    def test_dismiss_sets_status_and_window(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        row = ledger.mark_dismissed(
            pid,
            actor_id="alice",
            reason="noisy",
            dismissal_seconds=3600,
            now=now,
        )
        self.assertEqual(row.status, ledger.ProposalStatus.DISMISSED)
        self.assertIsNotNone(row.dismissed_until)

        self.assertEqual(ledger.list_active(now=now), [])
        decisions_by_kind = {d["decision"] for d in ledger.list_decisions(pid)}
        self.assertIn("dismissed", decisions_by_kind)

    def test_dismiss_suppresses_identical_rule_hash_within_window(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        ledger.mark_dismissed(
            pid,
            actor_id="alice",
            reason="noisy",
            dismissal_seconds=3600,
            now=now,
        )

        later = now + timedelta(minutes=15)
        result = ledger.upsert_pending(
            [
                _make_rule_proposal(
                    snapshot_ts="2026-04-22T10:15:00+00:00",
                    expires_at="2026-04-22T10:20:00+00:00",
                )
            ],
            now=later,
        )
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(result.persisted, [])
        self.assertEqual(ledger.list_active(now=later), [])

    def test_suppression_expires_after_window(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        ledger.mark_dismissed(
            pid,
            actor_id="alice",
            reason="noisy",
            dismissal_seconds=3600,
            now=now,
        )

        after_window = now + timedelta(hours=2)
        result = ledger.upsert_pending(
            [
                _make_rule_proposal(
                    snapshot_ts="2026-04-22T12:00:00+00:00",
                    expires_at="2026-04-22T12:05:00+00:00",
                )
            ],
            now=after_window,
        )
        self.assertEqual(len(result.persisted), 1)
        self.assertEqual(result.suppressed, [])

    def test_dismissal_only_suppresses_same_rule_hash(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending(
            [_make_rule_proposal(proposed_rule={"buy_below": -1.2, "sell_above": 1.4})],
            now=now,
        )
        pid = ledger.list_active(now=now)[0].proposal_id
        ledger.mark_dismissed(
            pid,
            actor_id="alice",
            reason="noisy",
            dismissal_seconds=3600,
            now=now,
        )

        later = now + timedelta(minutes=10)
        result = ledger.upsert_pending(
            [
                _make_rule_proposal(
                    proposed_rule={"buy_below": -2.0, "sell_above": 2.0},
                    snapshot_ts="2026-04-22T10:10:00+00:00",
                    expires_at="2026-04-22T10:15:00+00:00",
                )
            ],
            now=later,
        )
        self.assertEqual(len(result.persisted), 1)
        self.assertEqual(result.suppressed, [])

    def test_apply_after_dismiss_raises(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        pid = ledger.list_active(now=now)[0].proposal_id
        ledger.mark_dismissed(pid, actor_id="alice", dismissal_seconds=3600, now=now)
        with self.assertRaises(ledger.LedgerError):
            ledger.mark_applied(pid, actor_id="alice", now=now)


class IntegrationBuildTests(LedgerTestBase):
    """Smoke: Phase 1 proposal builders still produce ledger-ingestible objects."""

    def test_build_rule_update_proposal_round_trip(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        proposal = build_rule_update_proposal(
            symbol="BTC_THB",
            current_rule={"buy_below": -1.0, "sell_above": 1.0},
            compare_rows=[
                {
                    "variant": "CURRENT",
                    "total_pnl_thb": 5.0,
                    "win_rate_percent": 50.0,
                    "trades": 10,
                    "fee_guardrail": "FEE_OK",
                    "bars": 100,
                    "rule": {"buy_below": -1.0, "sell_above": 1.0},
                },
                {
                    "variant": "WIDER",
                    "total_pnl_thb": 25.0,
                    "win_rate_percent": 65.0,
                    "trades": 12,
                    "fee_guardrail": "FEE_OK",
                    "bars": 100,
                    "rule": {"buy_below": -1.5, "sell_above": 1.8},
                },
            ],
            freshness_status="Fresh",
        )
        self.assertIsNotNone(proposal)
        result = ledger.upsert_pending([proposal], now=now)
        self.assertEqual(len(result.persisted), 1)

    def test_build_prune_proposal_round_trip(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        proposal = build_prune_proposal(
            symbol="DOGE_THB",
            operational_state={
                "open_buy_count": 0,
                "open_sell_count": 0,
                "reserved_thb": 0.0,
                "reserved_coin": 0.0,
                "partial_fill": False,
                "review_reasons": [],
            },
            tuning_row={"recommendation": "PRUNE", "confidence": "HIGH_PRUNE"},
            baseline_pnl_thb=-8.0,
            best_pnl_thb=-8.0,
            fee_guardrail="THIN_EDGE",
        )
        result = ledger.upsert_pending([proposal], now=now)
        self.assertEqual(len(result.persisted), 1)
        active = ledger.list_active(kind=ProposalKind.PRUNE.value)
        self.assertEqual(active[0].symbol, "DOGE_THB")


class RunStartupSweepTests(LedgerTestBase):
    def test_sweep_marks_expired_when_not_throttled(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        future = now + timedelta(minutes=10)

        outcome = ledger.run_startup_sweep(now=future)

        self.assertFalse(outcome["skipped"])
        self.assertEqual(len(outcome["expired_ids"]), 1)
        self.assertEqual(outcome["last_sweep_at"], future)

    def test_sweep_throttled_within_min_interval(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        future = now + timedelta(minutes=10)

        # First sweep runs.
        first = ledger.run_startup_sweep(now=future, min_interval_seconds=60)
        self.assertFalse(first["skipped"])

        # Second sweep 30s later is throttled.
        shortly_after = future + timedelta(seconds=30)
        throttled = ledger.run_startup_sweep(
            now=shortly_after,
            min_interval_seconds=60,
            last_sweep_at=first["last_sweep_at"],
        )
        self.assertTrue(throttled["skipped"])
        self.assertEqual(throttled["expired_ids"], [])

    def test_sweep_runs_again_after_min_interval_elapses(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        ledger.upsert_pending([_make_rule_proposal()], now=now)
        future = now + timedelta(minutes=10)

        first = ledger.run_startup_sweep(now=future, min_interval_seconds=60)
        later = future + timedelta(seconds=120)

        second = ledger.run_startup_sweep(
            now=later,
            min_interval_seconds=60,
            last_sweep_at=first["last_sweep_at"],
        )
        self.assertFalse(second["skipped"])
        # Nothing left to expire on the second run.
        self.assertEqual(second["expired_ids"], [])

    def test_sweep_accepts_iso_string_last_sweep(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        outcome = ledger.run_startup_sweep(
            now=now,
            min_interval_seconds=60,
            last_sweep_at="2026-04-22T09:59:30+00:00",
        )
        self.assertTrue(outcome["skipped"])


if __name__ == "__main__":
    unittest.main()
