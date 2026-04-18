from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

from services import audit_service, db_service


class AuditLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_db_path = db_service.DB_PATH
        self._original_db_dir = db_service.DB_DIR
        self._original_audit_fallback_path = audit_service.AUDIT_FALLBACK_PATH
        self._temp_dir = Path.cwd() / "data" / "test_audit_logging"
        shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        db_service.DB_PATH = self._temp_dir / "bitkub.db"
        db_service.DB_DIR = db_service.DB_PATH.parent
        audit_service.AUDIT_FALLBACK_PATH = self._temp_dir / "audit_events.jsonl"
        db_service.init_db()

    def tearDown(self) -> None:
        db_service.DB_PATH = self._original_db_path
        db_service.DB_DIR = self._original_db_dir
        audit_service.AUDIT_FALLBACK_PATH = self._original_audit_fallback_path
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_config_audit_redacts_sensitive_values(self) -> None:
        old_config = {
            "mode": "paper",
            "api_secret": "old-secret",
            "nested": {
                "token": "old-token",
            },
        }
        new_config = {
            "mode": "live",
            "api_secret": "new-secret",
            "nested": {
                "token": "new-token",
            },
        }

        changed_fields = audit_service.audit_config_change(
            old_config=old_config,
            new_config=new_config,
            actor_type="ui",
            message="Saved system settings",
            source="streamlit_ui",
        )

        self.assertIn("mode", changed_fields)
        rows = db_service.fetch_recent_audit_events(limit=1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["action_type"], "config_update")
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["old_value"]["mode"], "paper")
        self.assertEqual(row["new_value"]["mode"], "live")
        self.assertEqual(row["old_value"]["api_secret"], audit_service.REDACTED_VALUE)
        self.assertEqual(row["new_value"]["api_secret"], audit_service.REDACTED_VALUE)
        self.assertEqual(
            row["old_value"]["nested.token"],
            audit_service.REDACTED_VALUE,
        )
        self.assertEqual(
            row["new_value"]["nested.token"],
            audit_service.REDACTED_VALUE,
        )

    def test_audit_event_falls_back_to_jsonl_when_sqlite_write_fails(self) -> None:
        with patch("services.audit_service.insert_audit_event", side_effect=RuntimeError("db offline")):
            audit_service.audit_event(
                action_type="runtime_startup",
                actor_type="system",
                source="startup",
                target_type="engine",
                target_id="main",
                status="succeeded",
                message="Bitkub engine startup completed",
            )

        self.assertTrue(audit_service.AUDIT_FALLBACK_PATH.exists())
        lines = audit_service.AUDIT_FALLBACK_PATH.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["action_type"], "runtime_startup")
        self.assertEqual(payload["fallback_reason"], "sqlite_write_failed")


if __name__ == "__main__":
    unittest.main()
