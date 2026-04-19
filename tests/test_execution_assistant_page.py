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

from ui.streamlit.refresh import AUTO_REFRESH_SAFE_PAGES, PAGE_ORDER


def _json_string(value: object) -> str:
    return repr(json.dumps(value, separators=(",", ":"), ensure_ascii=True))


def _base_config() -> dict[str, object]:
    return {
        "mode": "paper",
        "live_slippage_tolerance_percent": 1.0,
        "rules": {
            "THB_TRX": {
                "buy_below": 9.5,
                "sell_above": 10.5,
                "budget_thb": 200.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 1,
            }
        },
    }


def _operational_state() -> dict[str, object]:
    return {
        "symbol": "THB_TRX",
        "state_summary": "open buy 1 | open sell 0 | reserved THB 100.00 | reserved coin 0.25000000",
        "risk_summary": "entry blocked | exit clear",
        "available_coin": 1.0,
        "reserved_coin": 0.25,
        "reserved_thb": 100.0,
        "open_buy_count": 1,
        "open_sell_count": 0,
        "partial_fill": False,
        "entry_blocked": True,
        "exit_blocked": False,
        "entry_block_reasons": ["open buy order exists"],
        "exit_block_reasons": [],
        "review_required": False,
        "review_reasons": [],
        "recent_guardrail_block": {
            "message": "entry guardrail blocked the order",
            "channel": "entry",
            "created_at": "2026-04-19 10:00:00",
        },
        "holdings_row": {},
        "exchange_open_order_count": 1,
        "exchange_open_orders": [],
        "local_open_orders": [{"symbol": "THB_TRX", "side": "buy"}],
        "findings": {},
        "symbol_findings": {},
    }


def _page_script(*, body: str, quote_fetched_at: str = "2026-04-19 10:00:00") -> str:
    return "\n".join(
        [
            "import sys",
            "import os",
            "import tempfile",
            "from pathlib import Path",
            "",
            "os.environ['BITKUB_DB_PATH'] = str(Path(tempfile.gettempdir()) / 'bitkub_execution_assistant_tests.db')",
            "sys.path.insert(0, str(Path.cwd()))",
            "",
            "import json",
            "import streamlit as st",
            "from services.db_service import init_db",
            "from ui.streamlit import execution_assistant",
            "",
            f"CONFIG = json.loads({_json_string(_base_config())})",
            "LATEST_PRICES = {'THB_TRX': 10.0}",
            "PRIVATE_CTX = {'account_snapshot': {}, 'client': object()}",
            "RUNTIME = {}",
            "init_db()",
            "",
            textwrap.dedent(body).strip(),
            "",
            f"execution_assistant.render_execution_assistant_page(config=CONFIG, private_ctx=PRIVATE_CTX, runtime=RUNTIME, latest_prices=LATEST_PRICES, quote_fetched_at={quote_fetched_at!r})",
            "",
        ]
    )


def _app_test_from_script(script: str) -> AppTest:
    script_hash = hashlib.md5(script.encode("utf-8")).hexdigest()
    script_path = _TEST_TEMP_DIR / f"execution_assistant_{script_hash}.py"
    script_path.write_text(script, encoding="utf-8")
    return AppTest.from_file(script_path)


AppTest.from_string = staticmethod(_app_test_from_script)  # type: ignore[method-assign]


class ExecutionAssistantPageTests(unittest.TestCase):
    def test_page_order_and_auto_refresh_include_execution_assistant(self) -> None:
        self.assertIn("Execution Assistant", PAGE_ORDER)
        self.assertIn("Execution Assistant", AUTO_REFRESH_SAFE_PAGES)

    def test_stale_quote_disables_snap_actions_and_save(self) -> None:
        script = _page_script(
            body=f"""
            execution_assistant.fetch_open_execution_orders = lambda: []
            execution_assistant.build_symbol_operational_state = lambda **kwargs: json.loads({_json_string(_operational_state())})
            execution_assistant.insert_runtime_event = lambda **kwargs: None
            execution_assistant.save_config_with_feedback = lambda *args, **kwargs: True
            execution_assistant.now_text = lambda: "2026-04-19 10:01:10"
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertTrue(at.button(key="execution_assistant_snap_buy").disabled)
        self.assertTrue(at.button(key="execution_assistant_snap_sell").disabled)
        self.assertTrue(at.button(key="execution_assistant_snap_both").disabled)
        self.assertTrue(at.button(key="execution_assistant_save_rule").disabled)

    def test_snap_both_updates_draft_rates_to_safe_band(self) -> None:
        script = _page_script(
            body=f"""
            execution_assistant.fetch_open_execution_orders = lambda: []
            execution_assistant.build_symbol_operational_state = lambda **kwargs: json.loads({_json_string(_operational_state())})
            execution_assistant.insert_runtime_event = lambda **kwargs: None
            execution_assistant.save_config_with_feedback = lambda *args, **kwargs: True
            execution_assistant.now_text = lambda: "2026-04-19 10:00:20"
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        at.button(key="execution_assistant_snap_both").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertAlmostEqual(float(at.number_input(key="execution_assistant_draft_buy_below").value), 9.9, places=8)
        self.assertAlmostEqual(float(at.number_input(key="execution_assistant_draft_sell_above").value), 10.1, places=8)

    def test_save_adjusted_rule_requires_explicit_click_and_persists_draft(self) -> None:
        script = _page_script(
            body=f"""
            execution_assistant.fetch_open_execution_orders = lambda: []
            execution_assistant.build_symbol_operational_state = lambda **kwargs: json.loads({_json_string(_operational_state())})
            execution_assistant.insert_runtime_event = lambda **kwargs: None
            execution_assistant.now_text = lambda: "2026-04-19 10:00:20"

            def save_capture(current_config, updated_config, success_title, **kwargs):
                st.session_state["saved_rule"] = dict(updated_config["rules"]["THB_TRX"])
                st.session_state["save_title"] = success_title
                return True

            execution_assistant.save_config_with_feedback = save_capture
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        self.assertNotIn("saved_rule", at.session_state)

        at.button(key="execution_assistant_snap_both").click()
        at.run(timeout=20)
        at.button(key="execution_assistant_save_rule").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertIn("saved_rule", at.session_state)
        self.assertAlmostEqual(float(at.session_state["saved_rule"]["buy_below"]), 9.9, places=8)
        self.assertAlmostEqual(float(at.session_state["saved_rule"]["sell_above"]), 10.1, places=8)
        self.assertEqual(at.session_state["save_title"], "Saved execution assistant pricing for THB_TRX")

    def test_navigation_buttons_queue_target_context(self) -> None:
        script = _page_script(
            body=f"""
            execution_assistant.fetch_open_execution_orders = lambda: []
            execution_assistant.build_symbol_operational_state = lambda **kwargs: json.loads({_json_string(_operational_state())})
            execution_assistant.insert_runtime_event = lambda **kwargs: None
            execution_assistant.save_config_with_feedback = lambda *args, **kwargs: True
            execution_assistant.now_text = lambda: "2026-04-19 10:00:20"
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        at.button(key="execution_assistant_open_compare").click()
        at.run(timeout=20)
        self.assertEqual(at.session_state["ui_page_autorun"], "Strategy")
        self.assertEqual(at.session_state["strategy_workspace_autorun"], "Compare")
        self.assertEqual(at.session_state["strategy_workspace_focus_symbol"], "THB_TRX")

        at = AppTest.from_string(script)
        at.run(timeout=20)
        at.button(key="execution_assistant_open_live_ops").click()
        at.run(timeout=20)
        self.assertEqual(at.session_state["ui_page_autorun"], "Live Ops")
        self.assertEqual(at.session_state["live_ops_focus_symbol"], "THB_TRX")


if __name__ == "__main__":
    unittest.main()
