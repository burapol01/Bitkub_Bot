from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from services import backup_service, db_service


class BackupRestorePhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        self._original_backup_db_path = backup_service.DB_PATH
        self._original_backup_state_path = backup_service.STATE_FILE_PATH
        self._original_backup_state_pending_path = backup_service.STATE_PENDING_PATH
        self._original_backup_config_path = backup_service.CONFIG_PATH
        self._original_backup_config_base_path = backup_service.CONFIG_BASE_PATH

        self._temp_root = Path(tempfile.mkdtemp(prefix="bitkub_backup_restore_"))
        self._db_path = self._temp_root / "data" / "bitkub.db"
        self._state_path = self._temp_root / "runtime_state.json"
        self._state_pending_path = self._temp_root / "runtime_state.pending.json"
        self._config_path = self._temp_root / "config.json"
        self._config_base_path = self._temp_root / "config.base.json"
        self._backup_root = self._temp_root / "backups"

        db_service.DB_PATH = self._db_path
        db_service.DB_DIR = self._db_path.parent
        backup_service.DB_PATH = self._db_path
        backup_service.STATE_FILE_PATH = self._state_path
        backup_service.STATE_PENDING_PATH = self._state_pending_path
        backup_service.CONFIG_PATH = self._config_path
        backup_service.CONFIG_BASE_PATH = self._config_base_path

        db_service.init_db()
        conn = db_service._connect()
        try:
            conn.execute(
                """
                INSERT INTO runtime_events (
                    created_at, event_type, severity, message, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("2026-04-17 10:00:00", "backup_test", "info", "backup smoke test", "{}"),
            )
            conn.commit()
        finally:
            conn.close()

        self._state_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "manual_pause": True,
                    "last_zones": {"THB_BTC": "BUY"},
                    "positions": {"THB_BTC": {"coin_qty": 1}},
                    "daily_stats": {"2026-04-17": {"THB_BTC": {"trades": 1}}},
                    "cooldowns": {},
                },
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        self._state_pending_path.write_text(
            json.dumps({"pending": True}, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        self._config_base_path.write_text(
            json.dumps(
                {
                    "mode": "paper",
                    "base_url": "https://api.bitkub.com",
                    "fee_rate": 0.0025,
                    "interval_seconds": 60,
                    "cooldown_seconds": 60,
                    "live_execution_enabled": False,
                    "live_auto_entry_enabled": False,
                    "live_auto_exit_enabled": False,
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
                        "symbol": "THB_BTC",
                        "side": "buy",
                        "order_type": "limit",
                        "amount_thb": 100.0,
                        "amount_coin": 0.01,
                        "rate": 1.0,
                    },
                    "watchlist_symbols": ["THB_BTC"],
                    "telegram_enabled": False,
                    "telegram_control_enabled": False,
                    "telegram_notify_events": ["config_reload"],
                    "archive_enabled": True,
                    "archive_dir": "data/archive",
                    "archive_format": "csv",
                    "archive_compression": "gzip",
                    "backup_dir": "backups",
                    "backup_retention_days": 90,
                    "backup_include_env_file": False,
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
                    "trade_log_file": "paper_trade_log.csv",
                    "rules": {
                        "THB_BTC": {
                            "buy_below": 1.0,
                            "sell_above": 1.1,
                            "budget_thb": 100.0,
                            "stop_loss_percent": 1.0,
                            "take_profit_percent": 2.0,
                            "max_trades_per_day": 1,
                        }
                    },
                },
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        self._config_path.write_text(
            json.dumps(
                {
                    "telegram_enabled": True,
                    "backup_include_env_file": False,
                },
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        backup_service.DB_PATH = self._original_backup_db_path
        backup_service.STATE_FILE_PATH = self._original_backup_state_path
        backup_service.STATE_PENDING_PATH = self._original_backup_state_pending_path
        backup_service.CONFIG_PATH = self._original_backup_config_path
        backup_service.CONFIG_BASE_PATH = self._original_backup_config_base_path
        shutil.rmtree(self._temp_root, ignore_errors=True)

    def test_backup_bundle_contains_manifest_and_restores_assets(self) -> None:
        summary = backup_service.create_runtime_backup(
            backup_dir_value=self._backup_root,
            backup_retention_days=90,
            include_env_file=False,
        )

        self.assertTrue(summary["success"])
        bundle_path = Path(summary["bundle_path"])
        self.assertTrue(bundle_path.exists())

        with zipfile.ZipFile(bundle_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            self.assertEqual(manifest["version"], 1)
            self.assertTrue(any(asset["kind"] == "sqlite_db" for asset in manifest["assets"]))

        conn = db_service._connect()
        try:
            conn.execute("DELETE FROM runtime_events")
            conn.execute(
                """
                INSERT INTO runtime_events (
                    created_at, event_type, severity, message, details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("2026-04-17 11:00:00", "corrupted", "error", "corrupted data", "{}"),
            )
            conn.commit()
        finally:
            conn.close()
        self._state_path.write_text("{}", encoding="utf-8")
        self._state_pending_path.write_text("{}", encoding="utf-8")
        self._config_path.write_text("{}", encoding="utf-8")

        restore_summary = backup_service.restore_runtime_backup(
            bundle_path_value=bundle_path,
            overwrite=True,
        )

        self.assertTrue(restore_summary["success"])
        self.assertGreaterEqual(len(restore_summary["restored_assets"]), 2)

        conn = db_service._connect()
        try:
            count = conn.execute("SELECT COUNT(*) AS count FROM runtime_events").fetchone()["count"]
        finally:
            conn.close()
        self.assertEqual(int(count), 1)

        restored_state = json.loads(self._state_path.read_text(encoding="utf-8"))
        self.assertTrue(restored_state["manual_pause"])
        self.assertEqual(restored_state["last_zones"]["THB_BTC"], "BUY")

        restored_config = json.loads(self._config_path.read_text(encoding="utf-8"))
        self.assertEqual(restored_config["telegram_enabled"], True)


if __name__ == "__main__":
    unittest.main()
