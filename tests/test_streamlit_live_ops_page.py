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
        "fee_rate": 0.0025,
        "live_execution_enabled": False,
        "live_auto_entry_enabled": False,
        "live_auto_exit_enabled": True,
        "live_max_order_thb": 1000.0,
        "live_min_thb_balance": 100.0,
        "live_slippage_tolerance_percent": 1.0,
        "live_manual_order": {
            "enabled": False,
            "symbol": "THB_TRX",
            "side": "buy",
            "order_type": "limit",
            "amount_thb": 100.0,
            "amount_coin": 0.0,
            "rate": 10.0,
        },
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


def _account_snapshot() -> dict[str, object]:
    return {
        "balances": {
            "ok": True,
            "data": {
                "result": {
                    "THB": {"available": 5000.0, "reserved": 0.0},
                    "TRX": {"available": 2.0, "reserved": 0.0},
                }
            },
        },
        "open_orders_meta": {"mode": "global", "requires_symbol": False},
        "open_orders": {},
    }


def _page_script(*, body: str, quote_fetched_at: str = "2026-04-19 10:00:00") -> str:
    return "\n".join(
        [
            "import sys",
            "import os",
            "import tempfile",
            "from pathlib import Path",
            "",
            "os.environ['BITKUB_DB_PATH'] = str(Path(tempfile.gettempdir()) / 'bitkub_live_ops_page_tests.db')",
            "sys.path.insert(0, str(Path.cwd()))",
            "",
            "import json",
            "import streamlit as st",
            "from services.db_service import init_db",
            "from ui.streamlit import ops_pages",
            "",
            f"CONFIG = json.loads({_json_string(_base_config())})",
            f"ACCOUNT_SNAPSHOT = json.loads({_json_string(_account_snapshot())})",
            "PRIVATE_CTX = {",
            "    'client': object(),",
            "    'account_snapshot': ACCOUNT_SNAPSHOT,",
            "    'private_api_capabilities': [],",
            "}",
            "RUNTIME = {'daily_stats': {}, 'manual_pause': False}",
            "LATEST_PRICES = {'THB_TRX': 10.0}",
            "init_db()",
            "",
            textwrap.dedent(body).strip(),
            "",
            f"ops_pages.render_live_ops_page(config=CONFIG, runtime=RUNTIME, private_ctx=PRIVATE_CTX, latest_prices=LATEST_PRICES, quote_fetched_at={quote_fetched_at!r}, auto_refresh_run_every=None)",
            "",
        ]
    )


def _app_test_from_script(script: str) -> AppTest:
    script_hash = hashlib.md5(script.encode("utf-8")).hexdigest()
    script_path = _TEST_TEMP_DIR / f"streamlit_live_ops_{script_hash}.py"
    script_path.write_text(script, encoding="utf-8")
    return AppTest.from_file(script_path)


AppTest.from_string = staticmethod(_app_test_from_script)  # type: ignore[method-assign]


class LiveOpsPageAppTests(unittest.TestCase):
    def test_exit_helper_shows_safe_band_and_prefills_manual_sell_form(self) -> None:
        script = _page_script(
            body="""
            ops_pages.calc_daily_totals = lambda daily_stats: (0, 0, 0, 0)
            ops_pages.build_live_execution_guardrails = lambda **kwargs: {
                "ready": True,
                "live_auto_exit_enabled": True,
                "live_auto_entry_enabled": False,
                "blocked_reasons": [],
                "live_execution_enabled": False,
            }
            ops_pages.fetch_execution_console_summary = lambda: {
                "open_orders": [],
                "recent_orders": [],
                "recent_events": [],
            }
            ops_pages.render_refreshable_fragment = lambda run_every, render_fn: render_fn()
            ops_pages._latest_auto_exit_slippage_block_row = lambda: {
                "created_at": "2026-04-19 10:00:00",
                "symbol": "THB_TRX",
                "exit_reason": "sell_guardrail",
                "request_rate": 10.8,
                "latest_price": 10.0,
                "amount_coin": 0.25,
                "details": {
                    "candidate": {"symbol": "THB_TRX", "amount_coin": 0.25, "rate": 10.8},
                    "guardrails": {"live_slippage_tolerance_percent": 1.0},
                },
            }
            ops_pages.build_exit_guardrail_resolution = lambda **kwargs: {
                "latest_live_price": 10.0,
                "deviation_percent": 8.0,
                "allowed_sell_band_low": 9.9,
                "allowed_sell_band_high": 10.1,
                "suggested_safe_sell_rate": 10.1,
                "quote_freshness": "fresh",
                "quote_age_seconds": 20,
                "quote_safe_for_suggestion": True,
            }
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        rendered = "\n".join(str(caption.value) for caption in at.caption)
        self.assertIn("Allowed sell band from latest live price: 9.90000000 to 10.10000000", rendered)
        self.assertIn("Suggested safe sell rate: 10.10000000", rendered)
        self.assertIn("Quote freshness: fresh (20s old)", rendered)

        at.button(key="exit_guardrail_use_safe_THB_TRX").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["live_ops_manual_symbol"], "THB_TRX")
        self.assertEqual(at.session_state["live_ops_manual_side"], "sell")
        self.assertAlmostEqual(float(at.session_state["live_ops_manual_amount_coin"]), 0.25, places=8)
        self.assertAlmostEqual(float(at.session_state["live_ops_manual_rate"]), 10.1, places=8)
        self.assertEqual(
            at.session_state["live_ops_feedback"]["title"],
            "Manual form prefilled from exit helper",
        )

    def test_exit_helper_disables_one_time_actions_when_quote_is_stale(self) -> None:
        script = _page_script(
            body="""
            ops_pages.calc_daily_totals = lambda daily_stats: (0, 0, 0, 0)
            ops_pages.build_live_execution_guardrails = lambda **kwargs: {
                "ready": True,
                "live_auto_exit_enabled": True,
                "live_auto_entry_enabled": False,
                "blocked_reasons": [],
                "live_execution_enabled": False,
            }
            ops_pages.fetch_execution_console_summary = lambda: {
                "open_orders": [],
                "recent_orders": [],
                "recent_events": [],
            }
            ops_pages.render_refreshable_fragment = lambda run_every, render_fn: render_fn()
            ops_pages._latest_auto_exit_slippage_block_row = lambda: {
                "created_at": "2026-04-19 10:00:00",
                "symbol": "THB_TRX",
                "exit_reason": "sell_guardrail",
                "request_rate": 10.8,
                "latest_price": 10.0,
                "amount_coin": 0.25,
                "details": {
                    "candidate": {"symbol": "THB_TRX", "amount_coin": 0.25, "rate": 10.8},
                    "guardrails": {"live_slippage_tolerance_percent": 1.0},
                },
            }
            ops_pages.build_exit_guardrail_resolution = lambda **kwargs: {
                "latest_live_price": 10.0,
                "deviation_percent": 8.0,
                "allowed_sell_band_low": 9.9,
                "allowed_sell_band_high": 10.1,
                "suggested_safe_sell_rate": None,
                "quote_freshness": "stale",
                "quote_age_seconds": 70,
                "quote_safe_for_suggestion": False,
            }
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertTrue(at.button(key="exit_guardrail_use_latest_THB_TRX").disabled)
        self.assertTrue(at.button(key="exit_guardrail_use_safe_THB_TRX").disabled)


if __name__ == "__main__":
    unittest.main()
