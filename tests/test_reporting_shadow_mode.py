from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from config import validate_config
from services import db_service
from services.execution_service import build_live_execution_guardrails


class _ConfiguredPrivateClient:
    def is_configured(self) -> bool:
        return True


def _valid_config(*, mode: str) -> dict[str, object]:
    return {
        "mode": mode,
        "base_url": "https://api.bitkub.com",
        "fee_rate": 0.0025,
        "interval_seconds": 60,
        "cooldown_seconds": 60,
        "live_execution_enabled": True,
        "live_auto_entry_enabled": True,
        "live_auto_exit_enabled": True,
        "live_auto_entry_require_ranking": True,
        "live_auto_entry_rank_resolution": "240",
        "live_auto_entry_rank_lookback_days": 14,
        "live_auto_entry_min_score": 50.0,
        "live_auto_entry_allowed_biases": ["bullish", "mixed"],
        "live_max_order_thb": 500.0,
        "live_min_thb_balance": 100.0,
        "live_slippage_tolerance_percent": 1.0,
        "live_daily_loss_limit_thb": 1000.0,
        "live_manual_order": {
            "enabled": False,
            "symbol": "THB_TRX",
            "side": "buy",
            "order_type": "limit",
            "amount_thb": 100.0,
            "amount_coin": 0.0001,
            "rate": 10.0,
        },
        "watchlist_symbols": ["THB_TRX"],
        "telegram_enabled": False,
        "telegram_control_enabled": False,
        "telegram_notify_events": ["config_reload"],
        "archive_enabled": True,
        "archive_dir": "data/archive",
        "archive_format": "csv",
        "archive_compression": "gzip",
        "market_snapshot_archive_enabled": True,
        "signal_log_archive_enabled": True,
        "account_snapshot_archive_enabled": True,
        "reconciliation_archive_enabled": True,
        "market_snapshot_hot_retention_days": 90,
        "signal_log_hot_retention_days": 180,
        "runtime_event_retention_days": 30,
        "account_snapshot_hot_retention_days": 90,
        "reconciliation_hot_retention_days": 90,
        "signal_log_file": "signal_log.csv",
        "trade_log_file": "trade_log.csv",
        "rules": {
            "THB_TRX": {
                "buy_below": 10.0,
                "sell_above": 11.0,
                "budget_thb": 100.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 1,
            }
        },
    }


class ShadowLiveConfigAndGuardrailTests(unittest.TestCase):
    def test_validate_config_accepts_shadow_live_mode(self) -> None:
        errors = validate_config(_valid_config(mode="shadow-live"))
        self.assertEqual(errors, [])

    def test_shadow_live_guardrails_mark_mode_and_ready(self) -> None:
        guardrails = build_live_execution_guardrails(
            config=_valid_config(mode="shadow-live"),
            trading_mode="shadow-live",
            private_client=_ConfiguredPrivateClient(),
            private_api_capabilities=["open_orders=OK"],
            manual_pause=False,
            safety_pause=False,
            total_realized_pnl_thb=0.0,
            available_balances={"THB": 500.0},
            strategy_execution_wired=True,
        )

        self.assertTrue(guardrails["ready"])
        self.assertTrue(guardrails["shadow_live_mode"])
        self.assertEqual(guardrails["mode"], "shadow-live")


class ReportingJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        self._temp_dir = Path(tempfile.mkdtemp(prefix="bitkub_shadow_report_tests_"))
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_reports_page_dataset_includes_recent_trade_journal(self) -> None:
        db_service.insert_trade_journal(
            created_at="2026-04-16 10:00:00",
            trading_mode="shadow-live",
            channel="auto_live_entry",
            status="shadow_recorded",
            symbol="THB_TRX",
            side="buy",
            signal_reason="BUY_ZONE_ENTRY",
            request_rate=10.0,
            latest_price=9.95,
            amount_thb=100.0,
            details={"shadow_live": True},
        )

        payload = db_service.fetch_reports_page_dataset(
            today="2026-04-16",
            days=14,
            symbol="THB_TRX",
        )

        journal_rows = list(payload["report"]["recent_trade_journal"])
        self.assertEqual(len(journal_rows), 1)
        self.assertEqual(journal_rows[0]["trading_mode"], "shadow-live")
        self.assertEqual(journal_rows[0]["status"], "shadow_recorded")
        self.assertEqual(journal_rows[0]["symbol"], "THB_TRX")


if __name__ == "__main__":
    unittest.main()
