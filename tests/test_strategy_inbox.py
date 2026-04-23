from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TEST_TEMP_DIR = Path.cwd() / ".tmp" / "streamlit_tests"
_TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_TEST_TEMP_DIR)
os.environ["TEMP"] = str(_TEST_TEMP_DIR)
os.environ["TMPDIR"] = str(_TEST_TEMP_DIR)
tempfile.tempdir = str(_TEST_TEMP_DIR)

from services import db_service, strategy_proposal_ledger as ledger
from services.strategy_proposal_service import (
    ProposalKind,
    ProposalTier,
    PruneProposal,
    RuleProposal,
)
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


class LedgerTestBase(unittest.TestCase):
    """Spin up a fresh SQLite DB per test so ledger writes stay isolated."""

    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        safe_name = self.id().replace(".", "_")
        self._temp_dir = Path.cwd() / "data" / "test_strategy_inbox_ledger" / safe_name
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)


def _rule_proposal(
    *,
    symbol: str = "THB_AAA",
    snapshot_ts: str = "2026-04-22T10:00:00+00:00",
    expires_at: str = "2026-04-22T10:05:00+00:00",
    tier: ProposalTier = ProposalTier.AUTO_APPROVE,
    proposed_rule: dict[str, Any] | None = None,
) -> RuleProposal:
    return RuleProposal(
        symbol=symbol,
        tier=tier,
        confidence=0.9,
        current_rule={"buy_below": -1.0, "sell_above": 1.0},
        proposed_rule=proposed_rule or {"buy_below": -1.2, "sell_above": 1.4},
        reason="strong uplift",
        warnings=[],
        hard_blocks=[],
        best_variant="FASTER_EXIT",
        baseline_pnl_thb=10.0,
        proposed_pnl_thb=25.0,
        edge_thb=15.0,
        win_rate_percent=65.0,
        trades=14,
        fee_guardrail="FEE_OK",
        freshness_status="Fresh",
        snapshot_ts=snapshot_ts,
        expires_at=expires_at,
    )


def _prune_proposal(
    *,
    symbol: str = "THB_DOGE",
    snapshot_ts: str = "2026-04-22T10:00:00+00:00",
    tier: ProposalTier = ProposalTier.AUTO_APPROVE,
) -> PruneProposal:
    return PruneProposal(
        symbol=symbol,
        tier=tier,
        confidence=0.8,
        reason="prune-flagged",
        warnings=[],
        hard_blocks=[],
        remove_from_watchlist=False,
        tuning_recommendation="PRUNE",
        baseline_pnl_thb=-5.0,
        best_pnl_thb=-5.0,
        snapshot_ts=snapshot_ts,
    )


class PersistRecomputeTests(LedgerTestBase):
    def test_persist_writes_updates_and_prunes_as_pending(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        snapshot = {
            "updates": [_rule_proposal()],
            "prunes": [_prune_proposal()],
            "resolution": "240",
            "lookback_days": 14,
        }
        result = strategy_inbox.persist_recompute_to_ledger(
            snapshot,
            resolution="240",
            lookback_days=14,
            now=now,
        )
        self.assertEqual(len(result.persisted), 2)

        active = strategy_inbox.load_active_inbox(now=now)
        self.assertEqual(len(active["updates"]), 1)
        self.assertEqual(len(active["prunes"]), 1)
        self.assertEqual(active["updates"][0].symbol, "THB_AAA")
        self.assertEqual(active["prunes"][0].symbol, "THB_DOGE")

    def test_reload_survives_session_reset(self) -> None:
        """Simulate a page refresh: persist once, then reload on a cold reader."""

        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        strategy_inbox.persist_recompute_to_ledger(
            {"updates": [_rule_proposal()], "prunes": []},
            resolution="240",
            lookback_days=14,
            now=now,
        )
        # Second call with no recompute data simulates reloading the page.
        active = strategy_inbox.load_active_inbox(now=now)
        self.assertEqual(len(active["updates"]), 1)
        self.assertEqual(active["updates"][0].confidence, 0.9)
        self.assertEqual(active["updates"][0].best_variant, "FASTER_EXIT")

    def test_reconstructed_proposal_carries_ledger_proposal_id(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        strategy_inbox.persist_recompute_to_ledger(
            {"updates": [_rule_proposal()], "prunes": []},
            resolution="240",
            lookback_days=14,
            now=now,
        )
        active = strategy_inbox.load_active_inbox(now=now)
        proposal = active["updates"][0]
        self.assertTrue(proposal.proposal_id)
        fetched = ledger.get(proposal.proposal_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.status, ledger.ProposalStatus.PENDING)


class ApplyActionTests(LedgerTestBase):
    def test_apply_rule_update_marks_ledger_applied(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        strategy_inbox.persist_recompute_to_ledger(
            {"updates": [_rule_proposal()], "prunes": []},
            resolution="240",
            lookback_days=14,
            now=now,
        )
        active = strategy_inbox.load_active_inbox(now=now)
        proposal = active["updates"][0]

        save_calls: list[dict[str, Any]] = []

        def fake_save(old, new, *args, **kwargs):  # noqa: ANN001 — stub
            save_calls.append({"new": new, "args": args, "kwargs": kwargs})

        outcome = strategy_inbox.apply_rule_update_action(
            config=_config(),
            proposals=active["updates"],
            selected_symbols=[proposal.symbol],
            save_config_fn=fake_save,
            now=now,
        )

        self.assertEqual(len(outcome["applied"]), 1)
        self.assertEqual(len(save_calls), 1)

        # Ledger row now terminal; load_active should no longer surface it.
        refreshed = strategy_inbox.load_active_inbox(now=now)
        self.assertEqual(refreshed["updates"], [])
        stored = ledger.get(proposal.proposal_id)
        self.assertEqual(stored.status, ledger.ProposalStatus.APPLIED)

    def test_apply_rule_update_skips_hard_blocked(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        blocked = _rule_proposal(tier=ProposalTier.BLOCKED)
        blocked.hard_blocks = ["no candle data"]
        strategy_inbox.persist_recompute_to_ledger(
            {"updates": [blocked], "prunes": []},
            resolution="240",
            lookback_days=14,
            now=now,
        )
        active = strategy_inbox.load_active_inbox(now=now)
        proposal = active["updates"][0]

        save_calls: list[Any] = []

        def fake_save(*args, **kwargs):  # noqa: ANN001 — stub
            save_calls.append((args, kwargs))

        outcome = strategy_inbox.apply_rule_update_action(
            config=_config(),
            proposals=active["updates"],
            selected_symbols=[proposal.symbol],
            save_config_fn=fake_save,
            now=now,
        )
        self.assertEqual(outcome["applied"], [])
        self.assertEqual(outcome["skipped"], [proposal.symbol])
        self.assertEqual(save_calls, [])
        self.assertEqual(
            ledger.get(proposal.proposal_id).status,
            ledger.ProposalStatus.PENDING,
        )

    def test_apply_prune_marks_ledger_applied(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        strategy_inbox.persist_recompute_to_ledger(
            {"updates": [], "prunes": [_prune_proposal()]},
            resolution="240",
            lookback_days=14,
            now=now,
        )
        active = strategy_inbox.load_active_inbox(now=now)
        proposal = active["prunes"][0]

        def fake_save(*args, **kwargs):  # noqa: ANN001 — stub
            return None

        config = _config()
        config["rules"]["THB_DOGE"] = {"buy_below": 1.0, "sell_above": 2.0}

        outcome = strategy_inbox.apply_prune_action(
            config=config,
            proposals=active["prunes"],
            selected_symbols=[proposal.symbol],
            remove_watchlist=False,
            save_config_fn=fake_save,
            now=now,
        )
        self.assertEqual(len(outcome["applied"]), 1)
        refreshed = strategy_inbox.load_active_inbox(now=now)
        self.assertEqual(refreshed["prunes"], [])
        self.assertEqual(
            ledger.get(proposal.proposal_id).status,
            ledger.ProposalStatus.APPLIED,
        )


class DismissActionTests(LedgerTestBase):
    def test_dismiss_marks_ledger_dismissed(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        strategy_inbox.persist_recompute_to_ledger(
            {"updates": [_rule_proposal()], "prunes": []},
            resolution="240",
            lookback_days=14,
            now=now,
        )
        active = strategy_inbox.load_active_inbox(now=now)
        proposal = active["updates"][0]

        dismissed = strategy_inbox.dismiss_proposals_action(
            proposals=active["updates"],
            selected_symbols=[proposal.symbol],
            now=now,
        )
        self.assertEqual(len(dismissed), 1)
        refreshed = strategy_inbox.load_active_inbox(now=now)
        self.assertEqual(refreshed["updates"], [])
        stored = ledger.get(proposal.proposal_id)
        self.assertEqual(stored.status, ledger.ProposalStatus.DISMISSED)

    def test_dismiss_suppresses_identical_rule_hash_on_next_recompute(self) -> None:
        now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=timezone.utc)
        strategy_inbox.persist_recompute_to_ledger(
            {"updates": [_rule_proposal()], "prunes": []},
            resolution="240",
            lookback_days=14,
            now=now,
        )
        active = strategy_inbox.load_active_inbox(now=now)
        strategy_inbox.dismiss_proposals_action(
            proposals=active["updates"],
            selected_symbols=[active["updates"][0].symbol],
            now=now,
        )

        later = datetime(2026, 4, 22, 10, 10, 0, tzinfo=timezone.utc)
        result = strategy_inbox.persist_recompute_to_ledger(
            {
                "updates": [
                    _rule_proposal(
                        snapshot_ts="2026-04-22T10:10:00+00:00",
                        expires_at="2026-04-22T10:15:00+00:00",
                    )
                ],
                "prunes": [],
            },
            resolution="240",
            lookback_days=14,
            now=later,
        )
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(result.persisted, [])
        refreshed = strategy_inbox.load_active_inbox(now=later)
        self.assertEqual(refreshed["updates"], [])


if __name__ == "__main__":
    unittest.main()
