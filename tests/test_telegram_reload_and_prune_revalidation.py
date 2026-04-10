from __future__ import annotations

import unittest
from unittest.mock import patch

from services.telegram_service import queue_telegram_notification
from ui.streamlit import pages
from utils.time_utils import now_text


class TelegramReloadNotificationTests(unittest.TestCase):
    @patch("services.telegram_service.insert_telegram_outbox")
    @patch("services.telegram_service.fetch_recent_telegram_command_log")
    @patch("services.telegram_service.fetch_recent_telegram_outbox")
    def test_config_reload_notification_skips_recent_direct_command_response(
        self,
        mock_recent_outbox,
        mock_recent_command_log,
        mock_insert_outbox,
    ) -> None:
        mock_recent_outbox.return_value = []
        recent_time = now_text()
        mock_recent_command_log.return_value = [
            {
                "created_at": recent_time,
                "response_text": "Reloaded config.json successfully\nNo effective config changes detected.",
            }
        ]

        queued = queue_telegram_notification(
            config={
                "telegram_enabled": True,
                "telegram_notify_events": ["config_reload"],
            },
            created_at="2026-04-10 10:00:05",
            event_type="config_reload",
            title="Reloaded config.json successfully",
            lines=["No effective config changes detected."],
            payload={},
        )

        self.assertFalse(queued)
        mock_insert_outbox.assert_not_called()

    @patch("services.telegram_service.insert_telegram_outbox")
    @patch("services.telegram_service.fetch_recent_telegram_command_log")
    @patch("services.telegram_service.fetch_recent_telegram_outbox")
    def test_non_reload_notification_still_queues_normally(
        self,
        mock_recent_outbox,
        mock_recent_command_log,
        mock_insert_outbox,
    ) -> None:
        mock_recent_outbox.return_value = []
        recent_time = now_text()
        mock_recent_command_log.return_value = [
            {
                "created_at": recent_time,
                "response_text": "Reloaded config.json successfully\nNo effective config changes detected.",
            }
        ]

        queued = queue_telegram_notification(
            config={
                "telegram_enabled": True,
                "telegram_notify_events": ["runtime_error"],
            },
            created_at="2026-04-10 10:00:05",
            event_type="runtime_error",
            title="Runtime error",
            lines=["boom"],
            payload={},
        )

        self.assertTrue(queued)
        mock_insert_outbox.assert_called_once()


class StrategyPruneRevalidationTests(unittest.TestCase):
    def test_revalidate_prune_blocked_symbols_clears_stale_local_order_after_refresh(self) -> None:
        with patch.object(
            pages,
            "fetch_open_execution_orders",
            return_value=[{"symbol": "THB_TRX", "state": "open"}],
        ):
            with patch.object(
                pages,
                "_refresh_open_execution_orders_for_ui",
                return_value=([], []),
            ) as mock_refresh:
                blocked_symbols, refresh_errors = pages._revalidate_prune_blocked_symbols(
                    symbols_to_prune=["THB_TRX"]
                )

        self.assertEqual(blocked_symbols, [])
        self.assertEqual(refresh_errors, [])
        mock_refresh.assert_called_once()

    def test_revalidate_prune_blocked_symbols_skips_refresh_when_nothing_is_blocked(self) -> None:
        with patch.object(pages, "fetch_open_execution_orders", return_value=[]):
            with patch.object(
                pages,
                "_refresh_open_execution_orders_for_ui",
            ) as mock_refresh:
                blocked_symbols, refresh_errors = pages._revalidate_prune_blocked_symbols(
                    symbols_to_prune=["THB_TRX"]
                )

        self.assertEqual(blocked_symbols, [])
        self.assertEqual(refresh_errors, [])
        mock_refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
