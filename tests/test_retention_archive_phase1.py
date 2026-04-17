from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from services import db_service
from utils.time_utils import format_time_text, now_dt


def _archive_config(*, archive_dir: Path) -> dict[str, object]:
    return {
        "archive_enabled": True,
        "archive_dir": str(archive_dir),
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
        "signal_log_hot_retention_days": 90,
        "account_snapshot_hot_retention_days": 90,
        "reconciliation_hot_retention_days": 90,
        "runtime_event_retention_days": 30,
    }


class RetentionArchivePhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        self._temp_dir = Path(tempfile.mkdtemp(prefix="bitkub_retention_phase1_"))
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_archive_then_cleanup_moves_older_rows_to_disk(self) -> None:
        archive_dir = self._temp_dir / "archive"
        config = _archive_config(archive_dir=archive_dir)
        old_dt = now_dt() - timedelta(days=120)
        recent_dt = now_dt()
        old_time = format_time_text(old_dt)
        recent_time = format_time_text(recent_dt)
        archive_day = old_dt.strftime("%Y-%m-%d")

        with db_service._connect() as conn:
            conn.execute(
                """
                INSERT INTO market_snapshots
                    (created_at, symbol, last_price, buy_below, sell_above, zone, status, trading_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (old_time, "THB_BTC", 100.0, 90.0, 110.0, "BUY", "active", "paper"),
            )
            conn.execute(
                """
                INSERT INTO market_snapshots
                    (created_at, symbol, last_price, buy_below, sell_above, zone, status, trading_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (recent_time, "THB_BTC", 120.0, 90.0, 110.0, "SELL", "active", "paper"),
            )
            conn.execute(
                """
                INSERT INTO signal_logs
                    (created_at, symbol, last_price, buy_below, sell_above, zone, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (old_time, "THB_ETH", 50.0, 40.0, 60.0, "WATCH", "active"),
            )
            conn.execute(
                """
                INSERT INTO signal_logs
                    (created_at, symbol, last_price, buy_below, sell_above, zone, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (recent_time, "THB_ETH", 55.0, 40.0, 60.0, "WATCH", "active"),
            )
            conn.execute(
                """
                INSERT INTO account_snapshots
                    (created_at, source, private_api_status, capabilities_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (old_time, "api", "ready", "[]", "{}"),
            )
            conn.execute(
                """
                INSERT INTO account_snapshots
                    (created_at, source, private_api_status, capabilities_json, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (recent_time, "api", "ready", "[]", "{}"),
            )
            conn.execute(
                """
                INSERT INTO reconciliation_results
                    (created_at, phase, status, warnings_json, positions_count, exchange_balances_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (old_time, "snapshot", "ok", "[]", 1, "{}"),
            )
            conn.execute(
                """
                INSERT INTO reconciliation_results
                    (created_at, phase, status, warnings_json, positions_count, exchange_balances_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (recent_time, "snapshot", "ok", "[]", 1, "{}"),
            )
            conn.execute(
                """
                INSERT INTO runtime_events
                    (created_at, event_type, severity, message, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (old_time, "old_event", "info", "old event", "{}"),
            )
            conn.execute(
                """
                INSERT INTO runtime_events
                    (created_at, event_type, severity, message, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (recent_time, "recent_event", "info", "recent event", "{}"),
            )

        archive_summary = db_service.archive_sqlite_retention(config=config)
        cleanup_summary = db_service.cleanup_sqlite_retention(config=config)
        retention_status = db_service.fetch_retention_status_summary()

        def row_count(table_name: str) -> int:
            with db_service._connect() as conn:
                return int(
                    conn.execute(
                        f"SELECT COUNT(*) AS count FROM {table_name}"
                    ).fetchone()["count"]
                )

        market_archive_path = (
            archive_dir
            / "market_snapshots"
            / archive_day[:4]
            / archive_day[5:7]
            / f"market_snapshots_{archive_day}.csv.gz"
        )

        self.assertTrue(market_archive_path.exists())
        self.assertGreaterEqual(int(archive_summary["archived_total"]), 4)
        self.assertGreaterEqual(int(cleanup_summary["deleted_total"]), 4)
        self.assertEqual(row_count("market_snapshots"), 1)
        self.assertEqual(row_count("signal_logs"), 1)
        self.assertEqual(row_count("account_snapshots"), 1)
        self.assertEqual(row_count("reconciliation_results"), 1)
        self.assertEqual(row_count("runtime_events"), 1)
        self.assertIsNotNone(retention_status["latest_archive_run"])
        self.assertEqual(
            retention_status["latest_archive_run"]["cleanup_status"],
            "deleted",
        )


if __name__ == "__main__":
    unittest.main()
