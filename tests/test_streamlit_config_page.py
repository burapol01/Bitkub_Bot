from __future__ import annotations

import hashlib
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path

_TEST_TEMP_DIR = Path.cwd() / ".tmp" / "streamlit_tests"
_TEST_TEMP_DIR.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = str(_TEST_TEMP_DIR)
os.environ["TEMP"] = str(_TEST_TEMP_DIR)
os.environ["TMPDIR"] = str(_TEST_TEMP_DIR)
tempfile.tempdir = str(_TEST_TEMP_DIR)

from streamlit.testing.v1 import AppTest


def _json_string(value: object) -> str:
    return repr(json.dumps(value, separators=(",", ":"), ensure_ascii=True))


def _base_config() -> dict[str, object]:
    return {
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
        "live_max_order_thb": 1000.0,
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
            "rate": 1.0,
        },
        "watchlist_symbols": ["THB_FF", "THB_SUMX", "THB_TRX"],
        "telegram_enabled": False,
        "telegram_control_enabled": False,
        "telegram_notify_events": [
            "config_reload",
            "manual_live_order",
        ],
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
        "signal_log_file": "logs/signals.jsonl",
        "trade_log_file": "logs/trades.csv",
        "rules": {
            "THB_FF": {
                "buy_below": 1.0,
                "sell_above": 1.1,
                "budget_thb": 100.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 1,
            },
            "THB_SUMX": {
                "buy_below": 2.0,
                "sell_above": 2.2,
                "budget_thb": 150.0,
                "stop_loss_percent": 1.2,
                "take_profit_percent": 2.4,
                "max_trades_per_day": 1,
            },
            "THB_TRX": {
                "buy_below": 10.0,
                "sell_above": 11.0,
                "budget_thb": 200.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 1,
            },
        },
    }


def _page_script(*, config: dict[str, object], body: str = "") -> str:
    return "\n".join(
        [
            "import sys",
            "from pathlib import Path",
            "",
            "sys.path.insert(0, str(Path.cwd()))",
            "",
            "import json",
            "import streamlit as st",
            "from ui.streamlit import config_support",
            "",
            f"CONFIG = json.loads({_json_string(config)})",
            "config_support.fetch_market_symbol_universe = lambda: {",
            "    'symbols': ['THB_FF', 'THB_SUMX', 'THB_TRX', 'THB_XLM'],",
            "    'rows': [],",
            "    'source_by_symbol': {},",
            "    'exchange_symbols': ['THB_FF', 'THB_SUMX', 'THB_TRX', 'THB_XLM'],",
            "    'non_exchange_symbols': [],",
            "    'non_exchange_rows': [],",
            "    'error': '',",
            "}",
            "config_support.fetch_open_execution_orders = lambda: []",
            "",
            textwrap.dedent(body).strip(),
            "",
            "config_support.render_config_page(config=CONFIG)",
            "",
        ]
    )


def _app_test_from_script(script: str) -> AppTest:
    script_hash = hashlib.md5(script.encode("utf-8")).hexdigest()
    script_path = _TEST_TEMP_DIR / f"streamlit_config_{script_hash}.py"
    script_path.write_text(script, encoding="utf-8")
    return AppTest.from_file(script_path)


AppTest.from_string = staticmethod(_app_test_from_script)  # type: ignore[method-assign]


class ConfigPageAppTests(unittest.TestCase):
    def test_config_page_shows_budget_and_live_order_help_text(self) -> None:
        at = AppTest.from_string(_page_script(config=_base_config()))
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        rendered_captions = "\n".join(str(caption.value) for caption in at.caption)
        self.assertIn("Budget THB = per-symbol order budget.", rendered_captions)
        self.assertIn("Live Max Order THB = cap per live order.", rendered_captions)
        self.assertIn(
            "Live Min THB Balance = minimum THB to keep in wallet.",
            rendered_captions,
        )

    def test_rule_editor_loads_missing_budget_with_default_and_saves_it(self) -> None:
        config = _base_config()
        config["rules"]["THB_TRX"].pop("budget_thb")
        script = _page_script(
            config=config,
            body="""
            def save_capture(current_config, updated_config, success_title, **kwargs):
                st.session_state["saved_rule"] = dict(updated_config["rules"]["THB_TRX"])
                st.session_state["save_title"] = success_title
                return True

            config_support.save_config_with_feedback = save_capture
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        at.selectbox(key="selected_rule_symbol").set_value("THB_TRX")
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertAlmostEqual(
            float(at.number_input(key="config_rule_editor_budget_thb").value),
            100.0,
            places=4,
        )

        at.number_input(key="config_rule_editor_budget_thb").set_value(275.0)
        next(button for button in at.button if button.label == "Save Rule").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertAlmostEqual(
            float(at.session_state["saved_rule"]["budget_thb"]),
            275.0,
            places=4,
        )
        self.assertEqual(at.session_state["save_title"], "Saved rule for THB_TRX")
        self.assertAlmostEqual(
            float(at.session_state["saved_rule"]["buy_below"]),
            10.0,
            places=8,
        )
        self.assertAlmostEqual(
            float(at.session_state["saved_rule"]["sell_above"]),
            11.0,
            places=8,
        )

    def test_bulk_budget_update_changes_only_selected_symbols(self) -> None:
        script = _page_script(
            config=_base_config(),
            body="""
            def save_capture(current_config, updated_config, success_title, **kwargs):
                st.session_state["saved_rules"] = dict(updated_config["rules"])
                st.session_state["save_title"] = success_title
                return True

            config_support.save_config_with_feedback = save_capture
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        at.multiselect(key="config_bulk_budget_symbols").set_value(["THB_FF", "THB_SUMX"])
        at.number_input(key="config_bulk_budget_budget_thb").set_value(333.0)
        next(button for button in at.button if button.label == "Apply Budget Update").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["save_title"], "Saved budget for 2 live rule(s)")
        saved_rules = at.session_state["saved_rules"]
        self.assertAlmostEqual(float(saved_rules["THB_FF"]["budget_thb"]), 333.0, places=4)
        self.assertAlmostEqual(float(saved_rules["THB_SUMX"]["budget_thb"]), 333.0, places=4)
        self.assertAlmostEqual(float(saved_rules["THB_TRX"]["budget_thb"]), 200.0, places=4)
        self.assertAlmostEqual(float(saved_rules["THB_FF"]["buy_below"]), 1.0, places=8)
        self.assertAlmostEqual(float(saved_rules["THB_SUMX"]["sell_above"]), 2.2, places=8)

    def test_bulk_budget_update_can_apply_to_all_live_rules(self) -> None:
        script = _page_script(
            config=_base_config(),
            body="""
            def save_capture(current_config, updated_config, success_title, **kwargs):
                st.session_state["saved_rules"] = dict(updated_config["rules"])
                st.session_state["save_title"] = success_title
                return True

            config_support.save_config_with_feedback = save_capture
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        at.checkbox(key="config_bulk_budget_apply_all").set_value(True)
        at.number_input(key="config_bulk_budget_budget_thb").set_value(444.0)
        next(button for button in at.button if button.label == "Apply Budget Update").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["save_title"], "Saved budget for 3 live rule(s)")
        saved_rules = at.session_state["saved_rules"]
        self.assertEqual(
            {symbol: float(rule["budget_thb"]) for symbol, rule in saved_rules.items()},
            {"THB_FF": 444.0, "THB_SUMX": 444.0, "THB_TRX": 444.0},
        )


if __name__ == "__main__":
    unittest.main()
