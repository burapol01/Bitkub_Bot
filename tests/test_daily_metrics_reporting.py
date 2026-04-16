from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from services import db_service


class DailyMetricsReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        self._temp_dir = Path(tempfile.mkdtemp(prefix="bitkub_daily_metrics_tests_"))
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def _insert_sample_history(self) -> None:
        db_service.insert_paper_trade_log(
            buy_time="2026-04-15 09:00:00",
            sell_time="2026-04-15 10:00:00",
            symbol="THB_TRX",
            exit_reason="SELL_ZONE",
            budget_thb=100.0,
            buy_fee_thb=1.0,
            net_budget_thb=99.0,
            buy_price=10.0,
            sell_price=11.31313131,
            coin_qty=9.9,
            gross_proceeds_thb=112.0,
            sell_fee_thb=1.0,
            net_proceeds_thb=111.0,
            pnl_thb=10.0,
            pnl_percent=10.0,
        )
        db_service.insert_paper_trade_log(
            buy_time="2026-04-16 09:00:00",
            sell_time="2026-04-16 10:00:00",
            symbol="THB_ETH",
            exit_reason="STOP_LOSS",
            budget_thb=100.0,
            buy_fee_thb=1.0,
            net_budget_thb=99.0,
            buy_price=10.0,
            sell_price=8.18181818,
            coin_qty=9.9,
            gross_proceeds_thb=81.0,
            sell_fee_thb=1.0,
            net_proceeds_thb=80.0,
            pnl_thb=-20.0,
            pnl_percent=-20.0,
        )
        db_service.insert_execution_order(
            created_at="2026-04-15 11:00:00",
            updated_at="2026-04-15 11:00:00",
            symbol="THB_TRX",
            side="buy",
            order_type="limit",
            state="filled",
            request_payload={"sym": "THB_TRX", "amt": 100.0, "rat": 10.0},
            response_payload={"result": {"rate": 10.0, "fee": 1.0}},
            guardrails={"mode": "live"},
            exchange_order_id="buy-1",
            exchange_client_id=None,
            message="filled buy",
        )
        db_service.insert_execution_order(
            created_at="2026-04-16 11:00:00",
            updated_at="2026-04-16 11:00:00",
            symbol="THB_TRX",
            side="sell",
            order_type="limit",
            state="filled",
            request_payload={"sym": "THB_TRX", "amt": 9.9, "rat": 11.0},
            response_payload={"result": {"rate": 11.0, "fee": 1.089}},
            guardrails={"mode": "live"},
            exchange_order_id="sell-1",
            exchange_client_id=None,
            message="filled sell",
        )

    def test_refresh_daily_performance_metrics_rebuilds_strategy_and_portfolio_rows(self) -> None:
        self._insert_sample_history()

        summary = db_service.refresh_daily_performance_metrics_from_history()

        self.assertEqual(summary["strategy_daily_metrics"], 3)
        self.assertEqual(summary["portfolio_daily_metrics"], 2)

        strategy_rows = db_service.fetch_strategy_daily_metrics(days=5000)
        live_row = next(
            row
            for row in strategy_rows
            if row["report_date"] == "2026-04-16"
            and row["strategy_key"] == "live_execution"
            and row["symbol"] == "THB_TRX"
        )
        self.assertEqual(live_row["closed_trades"], 1)
        self.assertAlmostEqual(float(live_row["realized_pnl_thb"]), 7.811, places=3)
        self.assertAlmostEqual(float(live_row["fee_thb"]), 2.089, places=3)
        self.assertAlmostEqual(float(live_row["turnover_thb"]), 208.9, places=3)

        portfolio_rows = db_service.fetch_portfolio_daily_metrics(days=5000)
        latest_row = next(row for row in portfolio_rows if row["report_date"] == "2026-04-16")
        self.assertEqual(latest_row["combined_closed_trades"], 2)
        self.assertAlmostEqual(float(latest_row["combined_realized_pnl_thb"]), -12.189, places=3)
        self.assertAlmostEqual(float(latest_row["cumulative_realized_pnl_thb"]), -2.189, places=3)
        self.assertAlmostEqual(float(latest_row["drawdown_thb"]), -12.189, places=3)

    def test_reports_page_dataset_includes_daily_metric_tables(self) -> None:
        self._insert_sample_history()

        payload = db_service.fetch_reports_page_dataset(
            today="2026-04-16",
            days=5000,
        )

        self.assertIn("portfolio_daily_metrics", payload)
        self.assertIn("strategy_daily_metrics", payload)
        self.assertEqual(len(payload["portfolio_daily_metrics"]), 2)
        self.assertEqual(len(payload["strategy_daily_metrics"]), 3)


if __name__ == "__main__":
    unittest.main()
