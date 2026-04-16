from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from services import db_service, strategy_lab_service


def _base_rule() -> dict[str, float | int]:
    return {
        "buy_below": 9.2,
        "sell_above": 10.8,
        "budget_thb": 100.0,
        "stop_loss_percent": 1.0,
        "take_profit_percent": 2.0,
        "max_trades_per_day": 1,
    }


class ValidationFrameworkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        self._temp_dir = Path(tempfile.mkdtemp(prefix="bitkub_validation_tests_"))
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def _insert_daily_candles(self, *, symbol: str, closes: list[float], start_day: str) -> None:
        start_dt = datetime.fromisoformat(start_day)
        candles: list[dict[str, float | int | str]] = []
        for offset, close_price in enumerate(closes):
            current_dt = start_dt + timedelta(days=offset)
            candles.append(
                {
                    "open_time": int(current_dt.timestamp()),
                    "open_at": current_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open_price": float(close_price),
                    "high_price": float(close_price) + 0.2,
                    "low_price": max(0.01, float(close_price) - 0.2),
                    "close_price": float(close_price),
                    "volume": 1000.0 + offset,
                }
            )
        db_service.upsert_market_candles(symbol=symbol, resolution="1D", candles=candles)

    def _insert_daily_snapshots(self, *, symbol: str, prices: list[float], start_day: str) -> None:
        start_dt = datetime.fromisoformat(start_day)
        for offset, price in enumerate(prices):
            current_dt = start_dt + timedelta(days=offset)
            db_service.insert_market_snapshot(
                created_at=current_dt.strftime("%Y-%m-%d %H:%M:%S"),
                symbol=symbol,
                last_price=float(price),
                buy_below=9.2,
                sell_above=10.8,
                zone="BUY" if price <= 9.2 else "SELL" if price >= 10.8 else "WAIT",
                status="test",
                trading_mode="paper",
            )

    def _seed_history(self) -> None:
        prices = [10.0, 9.0, 11.0, 9.0, 11.0, 9.0, 11.0, 9.0, 11.0, 9.0]
        self._insert_daily_candles(symbol="THB_TRX", closes=prices, start_day="2026-01-01")
        self._insert_daily_snapshots(symbol="THB_TRX", prices=prices, start_day="2026-01-01")
        self._insert_daily_candles(
            symbol="THB_ETH",
            closes=[10.0, 10.1, 10.2, 10.3, 10.2, 10.1, 10.0, 9.9, 9.8, 9.7],
            start_day="2026-01-01",
        )

    def test_walk_forward_validation_persists_runs_and_slices(self) -> None:
        self._seed_history()

        result = strategy_lab_service.run_walk_forward_validation(
            symbol="THB_TRX",
            data_source="candles",
            resolution="1D",
            mode="deterministic_variants",
            date_from="2026-01-01",
            date_to="2026-01-10",
            train_window_days=3,
            test_window_days=2,
            step_days=2,
            base_rule=_base_rule(),
            fee_rate=0.01,
            cooldown_seconds=0,
            persist=True,
        )

        self.assertEqual(result["summary"]["validation_type"], "walk_forward")
        self.assertEqual(result["summary"]["total_slices"], 3)
        self.assertGreaterEqual(result["summary"]["completed_slices"], 1)

        runs = db_service.fetch_recent_validation_runs(
            limit=5,
            symbol="THB_TRX",
            validation_type="walk_forward",
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "completed")

        slices = db_service.fetch_validation_run_slices(validation_run_id=int(runs[0]["id"]))
        self.assertEqual(len(slices), 3)
        self.assertLess(str(slices[0]["train_start_at"]), str(slices[0]["train_end_at"]))
        self.assertEqual(str(slices[0]["train_end_at"]), str(slices[0]["test_start_at"]))

        reports_payload = db_service.fetch_reports_page_dataset(today="2026-01-10", days=30)
        self.assertEqual(len(reports_payload["recent_validation_runs"]), 1)
        self.assertEqual(len(reports_payload["recent_validation_slices"]), 3)

    def test_time_series_cv_uses_expanding_train_windows(self) -> None:
        self._seed_history()

        windows = strategy_lab_service.generate_time_series_cv_windows(
            date_from="2026-01-01",
            date_to="2026-01-10",
            train_window_days=3,
            test_window_days=2,
            step_days=2,
        )
        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0]["train_start_at"], windows[1]["train_start_at"])
        self.assertLess(windows[0]["train_end_at"], windows[1]["train_end_at"])

        result = strategy_lab_service.run_time_series_cross_validation(
            symbol="THB_TRX",
            data_source="candles",
            resolution="1D",
            mode="current_rule",
            date_from="2026-01-01",
            date_to="2026-01-10",
            train_window_days=3,
            test_window_days=2,
            step_days=2,
            base_rule=_base_rule(),
            fee_rate=0.01,
            cooldown_seconds=0,
            persist=True,
        )

        self.assertEqual(result["summary"]["validation_type"], "time_series_cv")
        runs = db_service.fetch_recent_validation_runs(
            limit=5,
            symbol="THB_TRX",
            validation_type="time_series_cv",
        )
        self.assertEqual(len(runs), 1)
        self.assertEqual(int((runs[0]["summary"] or {}).get("total_slices", 0) or 0), 3)

    def test_backtest_consistency_check_is_persisted_and_passes_for_deterministic_replay(self) -> None:
        self._seed_history()

        result = strategy_lab_service.run_backtest_consistency_check(
            symbol="THB_TRX",
            data_source="candles",
            resolution="1D",
            rule=_base_rule(),
            fee_rate=0.01,
            cooldown_seconds=0,
            start_at="2026-01-01 00:00:00",
            end_at="2026-01-06 00:00:00",
            repetitions=3,
            persist=True,
        )

        self.assertEqual(result["status"], "passed")
        self.assertEqual(len(set(result["hashes"])), 1)

        checks = db_service.fetch_recent_validation_consistency_checks(
            limit=5,
            symbol="THB_TRX",
        )
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["status"], "passed")

    def test_no_lookahead_replay_and_ranking_ignore_future_rows(self) -> None:
        self._seed_history()

        before_replay = strategy_lab_service.run_market_candle_replay(
            symbol="THB_TRX",
            resolution="1D",
            rule=_base_rule(),
            fee_rate=0.01,
            cooldown_seconds=0,
            days=30,
            start_at="2026-01-01 00:00:00",
            end_at="2026-01-05 00:00:00",
        )
        before_snapshot_replay = strategy_lab_service.run_market_snapshot_replay(
            symbol="THB_TRX",
            rule=_base_rule(),
            fee_rate=0.01,
            cooldown_seconds=0,
            days=30,
            start_at="2026-01-01 00:00:00",
            end_at="2026-01-05 00:00:00",
        )
        before_ranking = strategy_lab_service.build_coin_ranking(
            symbols=["THB_TRX", "THB_ETH"],
            resolution="1D",
            lookback_days=30,
            end_at="2026-01-05 00:00:00",
        )

        self._insert_daily_candles(
            symbol="THB_TRX",
            closes=[50.0, 100.0],
            start_day="2026-01-11",
        )
        self._insert_daily_snapshots(
            symbol="THB_TRX",
            prices=[50.0, 100.0],
            start_day="2026-01-11",
        )
        self._insert_daily_candles(
            symbol="THB_ETH",
            closes=[500.0, 900.0],
            start_day="2026-01-11",
        )

        after_replay = strategy_lab_service.run_market_candle_replay(
            symbol="THB_TRX",
            resolution="1D",
            rule=_base_rule(),
            fee_rate=0.01,
            cooldown_seconds=0,
            days=30,
            start_at="2026-01-01 00:00:00",
            end_at="2026-01-05 00:00:00",
        )
        after_snapshot_replay = strategy_lab_service.run_market_snapshot_replay(
            symbol="THB_TRX",
            rule=_base_rule(),
            fee_rate=0.01,
            cooldown_seconds=0,
            days=30,
            start_at="2026-01-01 00:00:00",
            end_at="2026-01-05 00:00:00",
        )
        after_ranking = strategy_lab_service.build_coin_ranking(
            symbols=["THB_TRX", "THB_ETH"],
            resolution="1D",
            lookback_days=30,
            end_at="2026-01-05 00:00:00",
        )

        self.assertEqual(before_replay["metrics"], after_replay["metrics"])
        self.assertEqual(before_replay["trades"], after_replay["trades"])
        self.assertEqual(before_snapshot_replay["metrics"], after_snapshot_replay["metrics"])
        self.assertEqual(before_snapshot_replay["trades"], after_snapshot_replay["trades"])
        self.assertEqual(before_ranking["rows"], after_ranking["rows"])


if __name__ == "__main__":
    unittest.main()
