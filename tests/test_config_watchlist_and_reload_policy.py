from __future__ import annotations

import unittest

from main import (
    _unsupported_live_entry_reason,
    should_queue_config_reload_telegram_notification,
    should_queue_safety_pause_telegram_notification,
)
from ui.streamlit.config_support import build_saved_watchlist_symbols


class ConfigWatchlistSaveTests(unittest.TestCase):
    def test_build_saved_watchlist_symbols_keeps_only_user_selection_and_fallback(self) -> None:
        saved_symbols = build_saved_watchlist_symbols(
            selected_watchlist=["THB_SUMX", "THB_TRX"],
            watchlist_fallback="THB_BTC, THB_SUMX, , THB_ETH",
        )

        self.assertEqual(saved_symbols, ["THB_SUMX", "THB_TRX", "THB_BTC", "THB_ETH"])

    def test_build_saved_watchlist_symbols_can_remove_previous_symbol(self) -> None:
        saved_symbols = build_saved_watchlist_symbols(
            selected_watchlist=["THB_TRX"],
            watchlist_fallback="",
        )

        self.assertEqual(saved_symbols, ["THB_TRX"])


class ConfigReloadTelegramPolicyTests(unittest.TestCase):
    def test_telegram_confirmation_reload_skips_extra_notification(self) -> None:
        self.assertFalse(
            should_queue_config_reload_telegram_notification(
                source="telegram_confirmation"
            )
        )

    def test_console_reload_keeps_notification(self) -> None:
        self.assertTrue(
            should_queue_config_reload_telegram_notification(source="console")
        )

    def test_telegram_confirmation_reload_skips_safety_pause_notification(self) -> None:
        self.assertFalse(
            should_queue_safety_pause_telegram_notification(
                source="telegram_confirmation"
            )
        )

    def test_runtime_safety_pause_keeps_notification(self) -> None:
        self.assertTrue(
            should_queue_safety_pause_telegram_notification(source="runtime")
        )


class UnsupportedLiveEntryReasonTests(unittest.TestCase):
    def test_market_source_reason_mentions_non_exchange_source(self) -> None:
        self.assertIn(
            "source=broker",
            _unsupported_live_entry_reason(
                source="market_source",
                error_message="broker",
            ),
        )

    def test_open_orders_probe_reason_is_specific(self) -> None:
        self.assertIn(
            "open_orders",
            _unsupported_live_entry_reason(source="open_orders_probe"),
        )

    def test_order_submit_reason_includes_error(self) -> None:
        self.assertIn(
            "Bitkub API error=61",
            _unsupported_live_entry_reason(
                source="order_submit",
                error_message="Bitkub API error=61 message=None",
            ),
        )


if __name__ == "__main__":
    unittest.main()
