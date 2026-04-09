from __future__ import annotations

import unittest

from main import should_queue_config_reload_telegram_notification
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


if __name__ == "__main__":
    unittest.main()
