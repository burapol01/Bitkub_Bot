from __future__ import annotations

import unittest

from main import (
    _unsupported_live_entry_reason,
    build_missing_position_block_lines,
    describe_missing_position_line,
    missing_position_cleanup_note,
    missing_position_symbols,
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


class MissingPositionReloadGateTests(unittest.TestCase):
    def _sample_position(self, **overrides):
        position = {
            "coin_qty": 1.23456789,
            "buy_price": 42.5,
            "budget_thb": 500.0,
            "buy_time": "2026-04-22 10:11:12",
            "entry_source": "wallet_import",
        }
        position.update(overrides)
        return position

    def test_missing_position_symbols_returns_sorted_diff(self) -> None:
        rules = {"THB_BTC": {}, "THB_ETH": {}}
        active_positions = {
            "THB_ETH": self._sample_position(),
            "THB_ZKC": self._sample_position(),
            "THB_ALPHA": self._sample_position(),
        }

        self.assertEqual(
            missing_position_symbols(rules, active_positions),
            ["THB_ALPHA", "THB_ZKC"],
        )

    def test_missing_position_symbols_allows_reload_when_no_positions_for_removed(self) -> None:
        rules = {"THB_BTC": {}}
        active_positions = {"THB_BTC": self._sample_position()}

        self.assertEqual(missing_position_symbols(rules, active_positions), [])

    def test_describe_missing_position_line_includes_qty_budget_and_source(self) -> None:
        line = describe_missing_position_line(
            prefix="open position still active for removed symbol:",
            symbol="THB_ALPHA",
            position=self._sample_position(),
            mode="paper",
        )

        self.assertIn("THB_ALPHA", line)
        self.assertIn("mode=paper", line)
        self.assertIn("local paper position", line)
        self.assertIn("qty=1.23456789", line)
        self.assertIn("buy_price=42.50000000", line)
        self.assertIn("budget_thb=500.00", line)
        self.assertIn("buy_time=2026-04-22 10:11:12", line)
        self.assertIn("entry_source=wallet_import", line)

    def test_describe_missing_position_line_handles_live_mode_tracking_label(self) -> None:
        line = describe_missing_position_line(
            prefix="open position still active for removed symbol:",
            symbol="THB_BTC",
            position=self._sample_position(),
            mode="live",
        )

        self.assertIn("mode=live", line)
        self.assertIn("local paper position shown for visibility", line)

    def test_describe_missing_position_line_handles_shadow_live_mode_tracking_label(self) -> None:
        line = describe_missing_position_line(
            prefix="open position still active for removed symbol:",
            symbol="THB_BTC",
            position=self._sample_position(),
            mode="shadow-live",
        )

        self.assertIn("mode=shadow-live", line)
        self.assertIn("local paper position shown for visibility", line)

    def test_describe_missing_position_line_tolerates_missing_fields(self) -> None:
        line = describe_missing_position_line(
            prefix="open position still active for removed symbol:",
            symbol="THB_ORPHAN",
            position={},
            mode="paper",
        )

        self.assertIn("qty=0.00000000", line)
        self.assertIn("budget_thb=0.00", line)
        self.assertIn("buy_time=unknown", line)
        self.assertIn("entry_source=unknown", line)

    def test_build_missing_position_block_lines_paper_reload_suggests_clear_hotkey(self) -> None:
        lines = build_missing_position_block_lines(
            prefix="open position still active for removed symbol:",
            removed_symbols=["THB_ALPHA", "THB_ZKC"],
            active_positions={
                "THB_ALPHA": self._sample_position(coin_qty=10.0),
                "THB_ZKC": self._sample_position(coin_qty=20.0, entry_source="strategy_buy"),
            },
            mode="paper",
            closing_note=missing_position_cleanup_note("paper", context="reload"),
        )

        self.assertEqual(len(lines), 3)
        self.assertIn("THB_ALPHA", lines[0])
        self.assertIn("THB_ZKC", lines[1])
        self.assertIn("strategy_buy", lines[1])
        self.assertIn("paper positions", lines[-1])
        self.assertIn("'c'", lines[-1])

    def test_build_missing_position_block_lines_live_reload_guides_local_paper_cleanup(self) -> None:
        lines = build_missing_position_block_lines(
            prefix="open position still active for removed symbol:",
            removed_symbols=["THB_BTC"],
            active_positions={"THB_BTC": self._sample_position()},
            mode="live",
            closing_note=missing_position_cleanup_note("live", context="reload"),
        )

        self.assertIn("mode=live", lines[-1])
        self.assertIn("local paper positions", lines[-1])
        self.assertIn("'c'", lines[-1])
        self.assertIn("Bitkub orders", lines[-1])

    def test_missing_position_cleanup_note_reload_read_only_mentions_switch_to_paper(self) -> None:
        note = missing_position_cleanup_note("read-only", context="reload")

        self.assertIn("mode=read-only", note)
        self.assertIn("switch to paper mode", note)
        self.assertIn("'c'", note)

    def test_missing_position_cleanup_note_reload_live_disabled_mentions_switch_to_paper(self) -> None:
        note = missing_position_cleanup_note("live-disabled", context="reload")

        self.assertIn("mode=live-disabled", note)
        self.assertIn("switch to paper mode", note)
        self.assertIn("'c'", note)

    def test_missing_position_cleanup_note_startup_paper_mentions_c_hotkey(self) -> None:
        note = missing_position_cleanup_note("paper", context="startup")
        self.assertIn("'c'", note)
        self.assertIn("reload", note)

    def test_missing_position_cleanup_note_startup_live_mentions_visibility_only(self) -> None:
        note = missing_position_cleanup_note("live", context="startup")
        self.assertIn("Mode=live", note)
        self.assertIn("visibility only", note)
        self.assertIn("'c'", note)
        self.assertIn("Bitkub orders", note)


if __name__ == "__main__":
    unittest.main()
