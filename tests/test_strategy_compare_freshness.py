from __future__ import annotations

import unittest
from unittest.mock import patch

from ui.streamlit import pages, strategy_support
from utils.time_utils import parse_time_text


def _base_variant() -> list[dict[str, object]]:
    return [
        {
            "variant": "CURRENT",
            "rule": {
                "buy_below": 10.0,
                "sell_above": 11.0,
                "budget_thb": 200.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 1,
            },
        }
    ]


class StrategyCompareFreshnessTests(unittest.TestCase):
    def tearDown(self) -> None:
        strategy_support.run_strategy_compare_rows.clear()

    def test_sync_success_invalidates_matching_compare_state(self) -> None:
        state: dict[str, object] = {
            "strategy_compare_payload": {
                "symbol": "THB_TRX",
                "source": "candles",
                "resolution": "240",
                "days": 14,
                "rows": [],
            }
        }

        invalidated_scopes = pages._invalidate_strategy_compare_state_for_candle_sync(
            sync_result={
                "resolution": "240",
                "days": 14,
                "synced": [
                    {"symbol": "THB_TRX", "resolution": "240"},
                    {"symbol": "THB_ETH", "resolution": "240"},
                ],
                "errors": [],
            },
            session_state=state,
            revision_value="rev-1",
        )

        self.assertEqual(invalidated_scopes, ["THB_TRX|candles|240|14"])
        self.assertNotIn("strategy_compare_payload", state)
        self.assertEqual(
            state[pages._strategy_compare_candle_revision_key(symbol="THB_TRX", resolution="240")],
            "rev-1",
        )
        self.assertEqual(
            state[pages._strategy_compare_candle_revision_key(symbol="THB_ETH", resolution="240")],
            "rev-1",
        )

    def test_compare_cache_token_refreshes_cached_compare_rows(self) -> None:
        replay_state = {"pnl": 10.0}
        replay_calls: list[dict[str, object]] = []

        def fake_run_market_candle_replay(**kwargs):
            replay_calls.append(dict(kwargs))
            pnl = float(replay_state["pnl"])
            return {
                "metrics": {
                    "trades": 1,
                    "wins": 1,
                    "losses": 0,
                    "win_rate_percent": 100.0,
                    "total_pnl_thb": pnl,
                    "avg_pnl_thb": pnl,
                    "profit_factor": 1.0,
                    "avg_hold_minutes": 5.0,
                },
                "coverage": {"last_seen": "2026-04-20 00:00:00"},
                "candles": 24,
                "open_position": None,
            }

        with patch.object(
            strategy_support,
            "run_market_candle_replay",
            side_effect=fake_run_market_candle_replay,
        ):
            first = strategy_support.run_strategy_compare_rows(
                symbol="THB_TRX",
                replay_source="candles",
                replay_resolution="240",
                lookback_days=14,
                fee_rate=0.0025,
                cooldown_seconds=60,
                variants=_base_variant(),
                cache_token="rev-1",
            )

            replay_state["pnl"] = 25.0

            second = strategy_support.run_strategy_compare_rows(
                symbol="THB_TRX",
                replay_source="candles",
                replay_resolution="240",
                lookback_days=14,
                fee_rate=0.0025,
                cooldown_seconds=60,
                variants=_base_variant(),
                cache_token="rev-1",
            )
            third = strategy_support.run_strategy_compare_rows(
                symbol="THB_TRX",
                replay_source="candles",
                replay_resolution="240",
                lookback_days=14,
                fee_rate=0.0025,
                cooldown_seconds=60,
                variants=_base_variant(),
                cache_token="rev-2",
            )

        self.assertEqual(len(replay_calls), 2)
        self.assertEqual(first[0]["total_pnl_thb"], 10.0)
        self.assertEqual(second[0]["total_pnl_thb"], 10.0)
        self.assertEqual(third[0]["total_pnl_thb"], 25.0)

    def test_compare_data_freshness_classifies_missing_aging_and_stale(self) -> None:
        checked_at = parse_time_text("2026-04-20 12:00:00")
        with patch.object(pages, "now_dt", return_value=checked_at):
            missing = pages._build_compare_data_freshness(
                source="candles",
                resolution="240",
                last_timestamp=None,
            )
            aging = pages._build_compare_data_freshness(
                source="candles",
                resolution="240",
                last_timestamp="2026-04-20 00:00:00",
            )
            stale = pages._build_compare_data_freshness(
                source="candles",
                resolution="240",
                last_timestamp="2026-04-18 00:00:00",
            )

        self.assertEqual(missing["status"], "Missing")
        self.assertEqual(aging["status"], "Aging")
        self.assertEqual(stale["status"], "Stale")

    def test_live_quote_freshness_classifies_fresh_stale_and_unavailable(self) -> None:
        checked_at = parse_time_text("2026-04-20 12:00:00")
        with patch.object(pages, "now_dt", return_value=checked_at):
            fresh = pages._build_live_quote_freshness(
                quote_fetched_at="2026-04-20 11:59:45"
            )
            stale = pages._build_live_quote_freshness(
                quote_fetched_at="2026-04-20 11:58:00"
            )
            unavailable = pages._build_live_quote_freshness(quote_fetched_at=None)

        self.assertEqual(fresh["status"], "fresh")
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(unavailable["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
