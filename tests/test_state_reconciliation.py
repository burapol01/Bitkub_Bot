from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from services import db_service, reconciliation_service, state_service
from utils.time_utils import now_dt, now_text


class RuntimeReconciliationFindingTests(unittest.TestCase):
    def test_collect_runtime_reconciliation_findings_captures_expected_categories(self) -> None:
        now = now_dt()
        execution_orders = [
            {
                "id": 1,
                "symbol": "THB_BTC",
                "state": "open",
                "exchange_order_id": "111",
                "updated_at": now.replace(microsecond=0).isoformat(),
            },
            {
                "id": 2,
                "symbol": "THB_ETH",
                "state": "open",
                "exchange_order_id": None,
                "updated_at": now.replace(microsecond=0).isoformat(),
            },
            {
                "id": 3,
                "symbol": "THB_XRP",
                "state": "partially_filled",
                "exchange_order_id": "333",
                "updated_at": now.replace(microsecond=0).isoformat(),
            },
            {
                "id": 4,
                "symbol": "THB_SOL",
                "state": "pending_submit",
                "exchange_order_id": "444",
                "updated_at": (now.replace(microsecond=0)).isoformat(),
            },
        ]
        execution_orders[0]["updated_at"] = (now.replace(microsecond=0)).isoformat()
        execution_orders[1]["updated_at"] = (now.replace(microsecond=0)).isoformat()
        execution_orders[2]["updated_at"] = (now.replace(microsecond=0)).isoformat()
        execution_orders[3]["updated_at"] = (now.replace(microsecond=0)).isoformat()
        execution_orders[0]["created_at"] = (now.replace(microsecond=0)).isoformat()
        execution_orders[1]["created_at"] = (now.replace(microsecond=0)).isoformat()
        execution_orders[2]["created_at"] = (now.replace(microsecond=0)).isoformat()
        execution_orders[3]["created_at"] = (now.replace(microsecond=0)).isoformat()

        stale_timestamp = "2026-04-18T00:00:00+07:00"
        execution_orders[0]["updated_at"] = stale_timestamp
        execution_orders[3]["updated_at"] = stale_timestamp

        account_snapshot = {
            "balances": {
                "ok": True,
                "result": {
                    "BTC": {"available": 0.0},
                    "DOGE": {"available": 0.0},
                    "ADA": {"available": 2.0},
                },
            },
            "open_orders": {
                "THB_DOGE": {"ok": True, "result": [{"id": "555", "side": "buy"}]},
                "THB_XRP": {"ok": True, "result": [{"id": "333", "side": "sell"}]},
            },
        }
        live_holdings_rows = [
            {
                "symbol": "THB_BTC",
                "available_qty": 0.0,
                "reserved_qty": 0.5,
                "last_execution_side": "buy",
            },
            {
                "symbol": "THB_DOGE",
                "available_qty": 0.0,
                "reserved_qty": 0.0,
                "last_execution_side": "",
            },
            {
                "symbol": "THB_ADA",
                "available_qty": 2.0,
                "reserved_qty": 0.0,
                "last_execution_side": "",
            },
        ]
        runtime_state_metadata = {
            "loaded_from_pending": True,
            "saved_at": "2026-04-16T00:00:00+07:00",
        }

        findings = reconciliation_service.collect_runtime_reconciliation_findings(
            execution_orders=execution_orders,
            live_holdings_rows=live_holdings_rows,
            account_snapshot=account_snapshot,
            runtime_state_metadata=runtime_state_metadata,
            stale_order_seconds=1,
            stale_runtime_state_seconds=1,
        )

        mismatch_counts = findings["mismatch_counts"]
        self.assertEqual(findings["account_sync_status"], "ready")
        self.assertEqual(findings["runtime_state_status"], "warning")
        self.assertGreaterEqual(mismatch_counts["missing_locally"], 1)
        self.assertGreaterEqual(mismatch_counts["missing_on_exchange"], 1)
        self.assertGreaterEqual(mismatch_counts["orders_without_exchange_id"], 1)
        self.assertGreaterEqual(mismatch_counts["stale_pending"], 1)
        self.assertGreaterEqual(mismatch_counts["partially_filled"], 1)
        self.assertGreaterEqual(mismatch_counts["reserved_without_open_order"], 1)
        self.assertGreaterEqual(mismatch_counts["open_order_without_reserved"], 1)
        self.assertGreaterEqual(mismatch_counts["unmanaged_live_holdings"], 1)
        self.assertGreaterEqual(mismatch_counts["runtime_state_stale"], 1)


class StateReconciliationStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        self._temp_dir = Path.cwd() / "data" / "test_state_reconciliation"
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_state_reconciliation_run_roundtrip(self) -> None:
        db_service.insert_state_reconciliation_run(
            created_at="2026-04-19T12:00:00+07:00",
            source="startup",
            status="warning",
            account_sync_status="partial",
            runtime_state_status="warning",
            local_open_orders_count=2,
            exchange_open_orders_count=1,
            corrected_order_count=1,
            unresolved_count=3,
            stale_pending_count=1,
            mismatch_summary={"missing_locally": 1, "stale_pending": 1},
            mismatch_details={"missing_locally": [{"symbol": "THB_BTC"}]},
            correction_summary={"corrected_orders": [{"execution_order_id": 7}]},
            notes={"exchange_snapshot_created_at": "2026-04-19T12:00:01+07:00"},
        )

        latest_run = db_service.fetch_latest_state_reconciliation_run()
        diagnostics_dataset = db_service.fetch_diagnostics_page_dataset()

        self.assertIsNotNone(latest_run)
        assert latest_run is not None
        self.assertEqual(latest_run["source"], "startup")
        self.assertEqual(latest_run["status"], "warning")
        self.assertEqual(latest_run["mismatch_summary"]["missing_locally"], 1)
        self.assertEqual(
            latest_run["correction_summary"]["corrected_orders"][0]["execution_order_id"],
            7,
        )
        self.assertEqual(
            diagnostics_dataset["latest_state_reconciliation"]["notes"][
                "exchange_snapshot_created_at"
            ],
            "2026-04-19T12:00:01+07:00",
        )


class RuntimeStateMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_state_file_path = state_service.STATE_FILE_PATH
        self._original_state_pending_path = state_service.STATE_PENDING_PATH
        self._temp_dir = Path.cwd() / "data" / "test_runtime_state_metadata"
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        state_service.STATE_FILE_PATH = self._temp_dir / "runtime_state.json"
        state_service.STATE_PENDING_PATH = self._temp_dir / "runtime_state.pending.json"

    def tearDown(self) -> None:
        state_service.STATE_FILE_PATH = self._original_state_file_path
        state_service.STATE_PENDING_PATH = self._original_state_pending_path
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_load_runtime_state_returns_saved_at_metadata(self) -> None:
        payload = {
            "version": 1,
            "saved_at": "2026-04-19T09:00:00+07:00",
            "manual_pause": True,
            "last_zones": {"THB_BTC": "BUY"},
            "positions": {"THB_BTC": {"coin_qty": 0.1}},
            "daily_stats": {
                "2026-04-19": {
                    "THB_BTC": {
                        "trades": 1,
                        "wins": 1,
                        "losses": 0,
                        "realized_pnl_thb": 1.0,
                    }
                }
            },
            "cooldowns": {},
        }
        state_service.STATE_FILE_PATH.write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

        manual_pause, messages, metadata = state_service.load_runtime_state(
            {},
            {},
            {},
            {},
        )

        self.assertTrue(manual_pause)
        self.assertIn("saved_at=2026-04-19T09:00:00+07:00", messages)
        self.assertEqual(metadata["saved_at"], "2026-04-19T09:00:00+07:00")
        self.assertFalse(metadata["loaded_from_pending"])
        self.assertEqual(metadata["open_positions"], 1)


if __name__ == "__main__":
    unittest.main()
