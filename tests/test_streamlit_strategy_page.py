from __future__ import annotations

import os
import json
import hashlib
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
        "watchlist_symbols": ["THB_FF", "THB_SUMX", "THB_TRX"],
        "fee_rate": 0.0025,
        "cooldown_seconds": 60,
        "market_snapshot_hot_retention_days": 90,
        "backup_dir": "backups",
        "backup_retention_days": 90,
        "backup_include_env_file": False,
        "live_auto_entry_min_score": 50.0,
        "live_auto_entry_allowed_biases": ["bullish", "mixed"],
        "live_min_thb_balance": 0.0,
        "live_max_order_thb": 1000.0,
    }


def _tuning_rows() -> list[dict[str, object]]:
    def row(symbol: str, recommendation: str = "PRUNE") -> dict[str, object]:
        return {
            "symbol": symbol,
            "recommendation": recommendation,
            "confidence": "HIGH_PRUNE" if recommendation == "PRUNE" else "REVIEW",
            "confidence_note": "Confidence note",
            "auto_entry_pass": "NO",
            "gate_reason": "Gate reason",
            "score": 42.0,
            "trend_bias": "mixed",
            "momentum_pct": 0.0,
            "last_close": 1.0,
            "market_context": "WAIT",
            "entry_gap_percent": 0.0,
            "target_gap_percent": 0.0,
            "stop_reference": 0.95,
            "stop_gap_percent": -5.0,
            "buy_below": 1.0,
            "sell_above": 1.1,
            "budget_thb": 100.0,
            "stop_loss_percent": 1.0,
            "take_profit_percent": 2.0,
            "max_trades_per_day": 1,
            "replay_trades": 4,
            "replay_pnl_thb": 10.0,
            "replay_total_fee_thb": 1.0,
            "replay_avg_fee_thb": 0.25,
            "replay_avg_pnl_thb": 2.5,
            "replay_fee_drag_percent": 10.0,
            "fee_guardrail": "OK",
            "fee_guardrail_note": "Fee note",
            "fee_guardrail_rank": 0,
            "replay_win_rate": 75.0,
            "replay_avg_hold_min": 15.0,
            "replay_open_position": "NO",
            "tuning_note": "Tuning note",
        }

    return [
        row("THB_FF"),
        row("THB_SUMX"),
    ]


def _compare_rows() -> list[dict[str, object]]:
    return [
        {
            "variant": "CURRENT",
            "buy_below": 10.0,
            "sell_above": 11.0,
            "budget_thb": 200.0,
            "stop_loss_percent": 1.0,
            "take_profit_percent": 2.0,
            "max_trades_per_day": 1,
            "trades": 6,
            "wins": 4,
            "losses": 2,
            "win_rate_percent": 66.67,
            "total_pnl_thb": 12.0,
            "avg_pnl_thb": 2.0,
            "profit_factor": 1.5,
            "avg_hold_minutes": 20.0,
            "open_position": "NO",
            "bars": 120,
            "coverage_last_seen": "2026-04-09 00:00:00",
            "note": "Current live rule from config.",
            "rule": {
                "buy_below": 10.0,
                "sell_above": 11.0,
                "budget_thb": 200.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 2.0,
                "max_trades_per_day": 1,
            },
            "decision": "Current baseline",
            "decision_reason": "Baseline",
            "decision_rank": -1,
            "total_fee_thb": 1.0,
            "avg_fee_thb": 0.1667,
            "fee_drag_percent": 7.69,
            "fee_guardrail": "OK",
            "fee_guardrail_note": "Fee note",
            "fee_guardrail_rank": 0,
        },
        {
            "variant": "FASTER_EXIT",
            "buy_below": 10.0,
            "sell_above": 10.6,
            "budget_thb": 200.0,
            "stop_loss_percent": 1.0,
            "take_profit_percent": 1.6,
            "max_trades_per_day": 1,
            "trades": 7,
            "wins": 5,
            "losses": 2,
            "win_rate_percent": 71.43,
            "total_pnl_thb": 15.0,
            "avg_pnl_thb": 2.14,
            "profit_factor": 1.8,
            "avg_hold_minutes": 12.0,
            "open_position": "NO",
            "bars": 120,
            "coverage_last_seen": "2026-04-09 00:00:00",
            "note": "Bring the sell target closer to realize gains sooner.",
            "rule": {
                "buy_below": 10.0,
                "sell_above": 10.6,
                "budget_thb": 200.0,
                "stop_loss_percent": 1.0,
                "take_profit_percent": 1.6,
                "max_trades_per_day": 1,
            },
            "decision": "Clearly better",
            "decision_reason": "PnL is higher than baseline.",
            "decision_rank": 0,
            "total_fee_thb": 1.0,
            "avg_fee_thb": 0.1429,
            "fee_drag_percent": 6.25,
            "fee_guardrail": "OK",
            "fee_guardrail_note": "Fee note",
            "fee_guardrail_rank": 0,
        },
    ]


def _app_script(
    *,
    workspace: str,
    body: str,
    latest_prices: dict[str, float] | None = None,
    quote_fetched_at: str = "2026-04-19 10:00:00",
) -> str:
    latest_prices = dict(latest_prices or {})
    return "\n".join(
        [
            "import sys",
            "import os",
            "import tempfile",
            "from pathlib import Path",
            "",
            "os.environ['BITKUB_DB_PATH'] = str(Path(tempfile.gettempdir()) / 'bitkub_streamlit_strategy_page_tests.db')",
            "sys.path.insert(0, str(Path.cwd()))",
            "",
            "import json",
            "import streamlit as st",
            "from services.db_service import init_db",
            "from ui.streamlit import pages",
            "",
            f"CONFIG = json.loads({_json_string(_base_config())})",
            f"LATEST_PRICES = json.loads({_json_string(latest_prices)})",
            "init_db()",
            "",
            textwrap.dedent(body).strip(),
            "",
            f"st.session_state['strategy_workspace'] = {workspace!r}",
            f"pages.render_strategy_page(config=CONFIG, latest_prices=LATEST_PRICES, quote_fetched_at={quote_fetched_at!r})",
            "",
        ]
    )


def _app_main_script(
    *,
    start_page: str,
    body: str,
    latest_prices: dict[str, float] | None = None,
    quote_fetched_at: str = "2026-04-19 10:00:00",
) -> str:
    latest_prices = dict(latest_prices or {})
    return "\n".join(
        [
            "import sys",
            "import os",
            "import tempfile",
            "from pathlib import Path",
            "",
            "os.environ['BITKUB_DB_PATH'] = str(Path(tempfile.gettempdir()) / 'bitkub_streamlit_strategy_page_tests.db')",
            "sys.path.insert(0, str(Path.cwd()))",
            "",
            "import json",
            "import streamlit as st",
            "from ui.streamlit import app, ops_pages, pages",
            "from services.db_service import init_db",
            "",
            f"CONFIG = json.loads({_json_string(_base_config())})",
            f"LATEST_PRICES = json.loads({_json_string(latest_prices)})",
            "init_db()",
            "",
            "if '_test_app_initialized' not in st.session_state:",
            "    st.session_state['_test_app_initialized'] = True",
            f"    st.session_state['ui_page'] = {start_page!r}",
            f"    st.query_params['page'] = {start_page!r}",
            "",
            "PRIVATE_CTX = {",
            "    'client': object(),",
            "    'account_snapshot': {},",
            "    'private_api_status': 'OK',",
            "    'private_api_capabilities': [],",
            "}",
            "DASHBOARD_CTX = {",
            "    'private_ctx': PRIVATE_CTX,",
            "    'runtime': {'daily_stats': {}, 'manual_pause': False},",
            "    'latest_prices': LATEST_PRICES,",
            f"    'quote_fetched_at': {quote_fetched_at!r},",
            "}",
            "OVERVIEW_CTX = {",
            "    'private_ctx': PRIVATE_CTX,",
            "    'runtime': {'daily_stats': {}},",
            "    'ticker_rows': [],",
            "}",
            "",
            "app.reload_config = lambda: (CONFIG, [])",
            "app.inject_css = lambda: None",
            "app.render_hero = lambda **kwargs: None",
            "app.render_deploy_refresh_watcher = lambda version_snapshot: None",
            "app.render_auto_refresh_controls = lambda selected_page: (False, None)",
            "app.get_auto_refresh_run_every = lambda selected_page, auto_refresh_enabled, auto_refresh_seconds: None",
            "app.render_auto_refresh_status = lambda selected_page, auto_refresh_enabled, auto_refresh_seconds: None",
            "app.build_dashboard_context = lambda config: DASHBOARD_CTX",
            "app.build_overview_context = lambda config: OVERVIEW_CTX",
            "app.sidebar_private_context = lambda: {",
            "    'private_api_status': 'OK',",
            "    'private_api_capabilities': [],",
            "}",
            "",
            textwrap.dedent(body).strip(),
            "",
            "app.main()",
            "",
        ]
    )


def _navigation_strategy_body(*, workspace: str, symbol: str = "THB_FF") -> str:
    compare_scope = f"{symbol}|candles|240|14"
    return f"""
    COMPARE_ROWS = json.loads({_json_string(_compare_rows())})
    COMPARE_VARIANTS = json.loads({_json_string([{"variant": row["variant"], "rule": row["rule"]} for row in _compare_rows()])})
    TUNING_ROWS = json.loads({_json_string(_tuning_rows())})
    OPEN_ORDERS = json.loads({_json_string([{"symbol": symbol, "side": "buy", "state": "open", "id": 1, "created_at": "2026-04-19 10:00:00", "updated_at": "2026-04-19 10:00:00"}])})

    pages.build_rule_seed = lambda config, symbol, market_price=None: dict(config["rules"][symbol])
    pages.build_rule_compare_variants = lambda base_rule: COMPARE_VARIANTS
    pages.annotate_strategy_compare_rows = lambda rows: rows
    pages.run_strategy_compare_rows = lambda **kwargs: COMPARE_ROWS
    pages.build_live_rule_tuning_rows = lambda **kwargs: TUNING_ROWS
    pages.fetch_open_execution_orders = lambda: OPEN_ORDERS
    pages.insert_runtime_event = lambda **kwargs: None
    pages.save_config_with_feedback = lambda *args, **kwargs: True
    pages._build_auto_entry_review_report = lambda limit=40: {{
        "events": [],
        "latest_context": {{}},
        "rejection_summary": [],
        "symbol_reject_summary": [],
        "symbol_candidate_summary": [],
        "top_candidates": [],
    }}
    pages._latest_strategy_compare_selection_map = lambda limit=200: {{
        {compare_scope!r}: {{
            "focus_variant": "FASTER_EXIT",
            "created_at": "2026-04-09 10:00:00",
        }}
    }}
    pages._latest_strategy_compare_applied_map = lambda limit=200: {{}}
    pages.build_symbol_operational_state = lambda **kwargs: {{
        "symbol": str(kwargs["symbol"]),
        "state_summary": "open buy 0 | open sell 0 | reserved THB 0.00 | reserved coin 0.00000000",
        "risk_summary": "entry clear | exit clear",
        "available_coin": 0.0,
        "reserved_coin": 0.0,
        "reserved_thb": 0.0,
        "open_buy_count": 0,
        "open_sell_count": 0,
        "partial_fill": False,
        "entry_blocked": False,
        "exit_blocked": False,
        "entry_block_reasons": [],
        "exit_block_reasons": [],
        "review_required": False,
        "review_reasons": [],
        "recent_guardrail_block": None,
        "holdings_row": {{}},
        "exchange_open_order_count": 0,
        "exchange_open_orders": [],
        "local_open_orders": [],
        "findings": {{}},
        "symbol_findings": {{}},
    }}

    ops_pages.calc_daily_totals = lambda daily_stats: (0, 0, 0, 0)
    ops_pages.fetch_execution_console_summary = lambda: {{
        "open_orders": OPEN_ORDERS,
        "recent_orders": OPEN_ORDERS,
        "recent_events": [],
    }}
    ops_pages.build_live_execution_guardrails = lambda **kwargs: {{
        "ready": True,
        "live_auto_exit_enabled": True,
        "live_auto_entry_enabled": True,
        "blocked_reasons": [],
        "live_execution_enabled": True,
    }}
    ops_pages.fetch_open_execution_orders = lambda: OPEN_ORDERS

    st.session_state.setdefault("strategy_workspace", {workspace!r})
    st.session_state.setdefault("strategy_compare_symbol", {symbol!r})
    st.session_state.setdefault("strategy_compare_symbol__input", {symbol!r})
    st.session_state.setdefault("strategy_compare_source", "candles")
    st.session_state.setdefault("strategy_compare_resolution", "240")
    st.session_state.setdefault("strategy_compare_days", 14)
    st.session_state.setdefault("strategy_tuning_focus_symbol", {symbol!r})
    """


def _navigation_live_ops_body(*, symbol: str = "THB_FF") -> str:
    compare_scope = f"{symbol}|candles|240|14"
    return f"""
    COMPARE_ROWS = json.loads({_json_string(_compare_rows())})
    COMPARE_VARIANTS = json.loads({_json_string([{"variant": row["variant"], "rule": row["rule"]} for row in _compare_rows()])})
    TUNING_ROWS = json.loads({_json_string(_tuning_rows())})
    OPEN_ORDERS = json.loads({_json_string([{"symbol": symbol, "side": "buy", "state": "open", "id": 1, "created_at": "2026-04-19 10:00:00", "updated_at": "2026-04-19 10:00:00"}])})

    pages.build_rule_seed = lambda config, symbol, market_price=None: dict(config["rules"][symbol])
    pages.build_rule_compare_variants = lambda base_rule: COMPARE_VARIANTS
    pages.annotate_strategy_compare_rows = lambda rows: rows
    pages.run_strategy_compare_rows = lambda **kwargs: COMPARE_ROWS
    pages.build_live_rule_tuning_rows = lambda **kwargs: TUNING_ROWS
    pages.fetch_open_execution_orders = lambda: OPEN_ORDERS
    pages.insert_runtime_event = lambda **kwargs: None
    pages.save_config_with_feedback = lambda *args, **kwargs: True
    pages._build_auto_entry_review_report = lambda limit=40: {{
        "events": [],
        "latest_context": {{}},
        "rejection_summary": [],
        "symbol_reject_summary": [],
        "symbol_candidate_summary": [],
        "top_candidates": [],
    }}
    pages._latest_strategy_compare_selection_map = lambda limit=200: {{
        {compare_scope!r}: {{
            "focus_variant": "FASTER_EXIT",
            "created_at": "2026-04-09 10:00:00",
        }}
    }}
    pages._latest_strategy_compare_applied_map = lambda limit=200: {{}}
    pages.build_symbol_operational_state = lambda **kwargs: {{
        "symbol": str(kwargs["symbol"]),
        "state_summary": "open buy 0 | open sell 0 | reserved THB 0.00 | reserved coin 0.00000000",
        "risk_summary": "entry clear | exit clear",
        "available_coin": 0.0,
        "reserved_coin": 0.0,
        "reserved_thb": 0.0,
        "open_buy_count": 0,
        "open_sell_count": 0,
        "partial_fill": False,
        "entry_blocked": False,
        "exit_blocked": False,
        "entry_block_reasons": [],
        "exit_block_reasons": [],
        "review_required": False,
        "review_reasons": [],
        "recent_guardrail_block": None,
        "holdings_row": {{}},
        "exchange_open_order_count": 0,
        "exchange_open_orders": [],
        "local_open_orders": [],
        "findings": {{}},
        "symbol_findings": {{}},
    }}

    ops_pages.calc_daily_totals = lambda daily_stats: (0, 0, 0, 0)
    ops_pages.fetch_execution_console_summary = lambda: {{
        "open_orders": OPEN_ORDERS,
        "recent_orders": OPEN_ORDERS,
        "recent_events": [],
    }}
    ops_pages.build_live_execution_guardrails = lambda **kwargs: {{
        "ready": True,
        "live_auto_exit_enabled": True,
        "live_auto_entry_enabled": True,
        "blocked_reasons": [],
        "live_execution_enabled": True,
    }}
    ops_pages.fetch_open_execution_orders = lambda: OPEN_ORDERS

    st.session_state.setdefault("strategy_workspace", "Live Tuning")
    st.session_state.setdefault("strategy_tuning_focus_symbol", {symbol!r})
    st.session_state.setdefault("strategy_tuning_focus_symbol__input", {symbol!r})
    st.session_state.setdefault("live_ops_focus_symbol", {symbol!r})
    st.session_state.setdefault("live_ops_manual_symbol", {symbol!r})
    """


def _app_test_from_script(script: str) -> AppTest:
    script_hash = hashlib.md5(script.encode("utf-8")).hexdigest()
    script_path = _TEST_TEMP_DIR / f"streamlit_strategy_{script_hash}.py"
    script_path.write_text(script, encoding="utf-8")
    return AppTest.from_file(script_path)


AppTest.from_string = staticmethod(_app_test_from_script)  # type: ignore[method-assign]


class StrategyPageAppTests(unittest.TestCase):
    def test_live_tuning_prune_submit_does_not_raise(self) -> None:
        script = _app_script(
            workspace="Live Tuning",
            body=f"""
            RANKING_PAYLOAD = {{"rows": [], "coverage": [], "errors": []}}
            TUNING_ROWS = json.loads({_json_string(_tuning_rows())})
            OPEN_ORDERS = json.loads({_json_string([{"symbol": "THB_FF", "state": "open"}])})
            AUTO_ENTRY_REPORT = {{
                "events": [],
                "latest_context": {{}},
                "rejection_summary": [],
                "symbol_reject_summary": [],
                "symbol_candidate_summary": [],
                "top_candidates": [],
            }}

            pages._cached_coin_ranking = lambda **kwargs: RANKING_PAYLOAD
            pages.build_live_rule_tuning_rows = lambda **kwargs: TUNING_ROWS
            pages.fetch_open_execution_orders = lambda: OPEN_ORDERS
            pages.insert_runtime_event = lambda **kwargs: None
            pages._build_auto_entry_review_report = lambda limit=40: AUTO_ENTRY_REPORT

            def save_once(*args, **kwargs):
                if st.session_state.get("_test_prune_saved"):
                    return False
                st.session_state["_test_prune_saved"] = True
                return True

            pages.save_config_with_feedback = save_once
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        prune_widget = at.multiselect(key="strategy_prune_live_rules_selection")
        self.assertEqual(list(prune_widget.options), ["THB_FF", "THB_SUMX"])
        self.assertEqual(list(prune_widget.value), ["THB_FF", "THB_SUMX"])

        submit_button = next(
            button for button in at.button if button.label == "Continue"
        )
        submit_button.click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)

    def test_compare_focus_restores_latest_persisted_variant(self) -> None:
        compare_scope = "THB_TRX|candles|240|14"
        script = _app_script(
            workspace="Compare",
            body=f"""
            COMPARE_ROWS = json.loads({_json_string(_compare_rows())})
            COMPARE_VARIANTS = json.loads({_json_string([{"variant": row["variant"], "rule": row["rule"]} for row in _compare_rows()])})

            pages.build_rule_seed = lambda config, symbol, market_price=None: dict(config["rules"][symbol])
            pages.build_rule_compare_variants = lambda base_rule: COMPARE_VARIANTS
            pages.annotate_strategy_compare_rows = lambda rows: rows
            pages.run_strategy_compare_rows = lambda **kwargs: COMPARE_ROWS
            pages.insert_runtime_event = lambda **kwargs: None
            pages._latest_strategy_compare_selection_map = lambda limit=200: {{
                {compare_scope!r}: {{
                    "focus_variant": "FASTER_EXIT",
                    "created_at": "2026-04-09 10:00:00",
                }}
            }}
            pages._latest_strategy_compare_applied_map = lambda limit=200: {{}}

            st.session_state["strategy_compare_symbol"] = "THB_TRX"
            st.session_state["strategy_compare_source"] = "candles"
            st.session_state["strategy_compare_resolution"] = "240"
            st.session_state["strategy_compare_days"] = 14
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        focus_key = "strategy_compare_focus_variant::THB_TRX|candles|240|14"
        apply_key = "strategy_compare_apply_variant::THB_TRX|candles|240|14"

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.selectbox(key=focus_key).value, "FASTER_EXIT")
        self.assertEqual(at.selectbox(key=apply_key).value, "FASTER_EXIT")

    def test_compare_symbol_selection_sticks_after_run_compare(self) -> None:
        script = _app_script(
            workspace="Compare",
            body=f"""
            COMPARE_ROWS = json.loads({_json_string(_compare_rows())})
            COMPARE_VARIANTS = json.loads({_json_string([{"variant": row["variant"], "rule": row["rule"]} for row in _compare_rows()])})

            def build_compare_rows(**kwargs):
                symbol = str(kwargs["symbol"])
                rows = json.loads(json.dumps(COMPARE_ROWS))
                for row in rows:
                    row["note"] = f"Compare payload for {{symbol}}"
                return rows

            pages.build_rule_seed = lambda config, symbol, market_price=None: dict(config["rules"][symbol])
            pages.build_rule_compare_variants = lambda base_rule: COMPARE_VARIANTS
            pages.annotate_strategy_compare_rows = lambda rows: rows
            pages.run_strategy_compare_rows = build_compare_rows
            pages.insert_runtime_event = lambda **kwargs: None
            pages._latest_strategy_compare_selection_map = lambda limit=200: {{}}
            pages._latest_strategy_compare_applied_map = lambda limit=200: {{}}

            st.session_state["strategy_compare_symbol"] = "THB_TRX"
            st.session_state["strategy_compare_source"] = "candles"
            st.session_state["strategy_compare_resolution"] = "240"
            st.session_state["strategy_compare_days"] = 14
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        at.selectbox(key="strategy_compare_symbol__input").set_value("THB_SUMX")
        next(button for button in at.button if button.label == "Run Compare").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.selectbox(key="strategy_compare_symbol__input").value, "THB_SUMX")
        rendered = "\n".join(str(markdown.value) for markdown in at.markdown)
        self.assertIn("Symbol THB_SUMX", rendered)

    def test_live_tuning_shows_live_price_overlay_for_focus_symbol(self) -> None:
        script = _app_script(
            workspace="Live Tuning",
            latest_prices={"THB_FF": 1.05},
            body=f"""
            RANKING_PAYLOAD = {{"rows": [], "coverage": [], "errors": []}}
            TUNING_ROWS = json.loads({_json_string(_tuning_rows())})
            AUTO_ENTRY_REPORT = {{
                "events": [],
                "latest_context": {{}},
                "rejection_summary": [],
                "symbol_reject_summary": [],
                "symbol_candidate_summary": [],
                "top_candidates": [],
            }}

            pages._cached_coin_ranking = lambda **kwargs: RANKING_PAYLOAD
            pages.build_live_rule_tuning_rows = lambda **kwargs: TUNING_ROWS
            pages.fetch_open_execution_orders = lambda: []
            pages.insert_runtime_event = lambda **kwargs: None
            pages._build_auto_entry_review_report = lambda limit=40: AUTO_ENTRY_REPORT
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        rendered = "\n".join(str(caption.value) for caption in at.caption)
        self.assertIn("Live price overlay: price=1.05000000", rendered)

    def test_compare_shows_live_price_overlay_for_selected_symbol(self) -> None:
        script = _app_script(
            workspace="Compare",
            latest_prices={"THB_TRX": 10.5},
            body=f"""
            COMPARE_ROWS = json.loads({_json_string(_compare_rows())})
            COMPARE_VARIANTS = json.loads({_json_string([{"variant": row["variant"], "rule": row["rule"]} for row in _compare_rows()])})

            pages.build_rule_seed = lambda config, symbol, market_price=None: dict(config["rules"][symbol])
            pages.build_rule_compare_variants = lambda base_rule: COMPARE_VARIANTS
            pages.annotate_strategy_compare_rows = lambda rows: rows
            pages.run_strategy_compare_rows = lambda **kwargs: COMPARE_ROWS
            pages.insert_runtime_event = lambda **kwargs: None
            pages._latest_strategy_compare_selection_map = lambda limit=200: {{}}
            pages._latest_strategy_compare_applied_map = lambda limit=200: {{}}

            st.session_state["strategy_compare_symbol"] = "THB_TRX"
            st.session_state["strategy_compare_source"] = "candles"
            st.session_state["strategy_compare_resolution"] = "240"
            st.session_state["strategy_compare_days"] = 14
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        rendered = "\n".join(str(caption.value) for caption in at.caption)
        self.assertIn("Live price overlay: price=10.50000000", rendered)

    def test_compare_open_live_ops_sets_focus_symbol(self) -> None:
        script = _app_main_script(
            start_page="Strategy",
            body=_navigation_strategy_body(workspace="Compare", symbol="THB_FF"),
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        next(button for button in at.button if button.label == "Open Live Ops").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["ui_page"], "Live Ops")
        self.assertEqual(at.session_state["sidebar_page"], "Live Ops")
        self.assertEqual(at.session_state["live_ops_focus_symbol"], "THB_FF")
        self.assertEqual(at.session_state["live_ops_manual_symbol"], "THB_FF")

    def test_compare_open_live_tuning_sets_workspace(self) -> None:
        script = _app_main_script(
            start_page="Strategy",
            body=_navigation_strategy_body(workspace="Compare", symbol="THB_FF"),
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        next(button for button in at.button if button.label == "Open Live Tuning").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["ui_page"], "Strategy")
        self.assertEqual(at.session_state["sidebar_page"], "Strategy")
        self.assertEqual(at.session_state["strategy_workspace"], "Live Tuning")
        self.assertEqual(at.session_state["strategy_tuning_focus_symbol"], "THB_FF")

    def test_live_tuning_open_compare_sets_workspace(self) -> None:
        script = _app_main_script(
            start_page="Strategy",
            body=_navigation_strategy_body(workspace="Live Tuning", symbol="THB_FF"),
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        next(button for button in at.button if button.label == "Open Compare").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["ui_page"], "Strategy")
        self.assertEqual(at.session_state["sidebar_page"], "Strategy")
        self.assertEqual(at.session_state["strategy_workspace"], "Compare")
        self.assertEqual(at.session_state["strategy_compare_symbol"], "THB_FF")

    def test_live_tuning_open_live_ops_sets_focus_symbol(self) -> None:
        script = _app_main_script(
            start_page="Strategy",
            body=_navigation_strategy_body(workspace="Live Tuning", symbol="THB_FF"),
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        next(button for button in at.button if button.label == "Open Live Ops").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["ui_page"], "Live Ops")
        self.assertEqual(at.session_state["sidebar_page"], "Live Ops")
        self.assertEqual(at.session_state["live_ops_focus_symbol"], "THB_FF")
        self.assertEqual(at.session_state["live_ops_manual_symbol"], "THB_FF")

    def test_live_ops_open_compare_switches_to_strategy_compare(self) -> None:
        script = _app_main_script(
            start_page="Live Ops",
            body=_navigation_live_ops_body(symbol="THB_FF")
            + "\n"
            + '    st.session_state.setdefault("strategy_compare_symbol", "THB_TRX")\n'
            + '    st.session_state.setdefault("strategy_compare_symbol__input", "THB_TRX")\n',
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        next(button for button in at.button if button.label == "Open Compare").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["ui_page"], "Strategy")
        self.assertEqual(at.session_state["sidebar_page"], "Strategy")
        self.assertEqual(at.session_state["strategy_workspace"], "Compare")
        self.assertEqual(at.session_state["strategy_compare_symbol"], "THB_FF")
        self.assertEqual(at.session_state["strategy_compare_symbol__input"], "THB_FF")

    def test_live_ops_open_live_tuning_switches_to_strategy_tuning(self) -> None:
        script = _app_main_script(
            start_page="Live Ops",
            body=_navigation_live_ops_body(symbol="THB_FF"),
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        next(button for button in at.button if button.label == "Open Live Tuning").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["ui_page"], "Strategy")
        self.assertEqual(at.session_state["sidebar_page"], "Strategy")
        self.assertEqual(at.session_state["strategy_workspace"], "Live Tuning")
        self.assertEqual(at.session_state["strategy_tuning_focus_symbol"], "THB_FF")

    def test_live_tuning_prune_shows_linked_order_actions(self) -> None:
        script = _app_script(
            workspace="Live Tuning",
            body=f"""
            RANKING_PAYLOAD = {{"rows": [], "coverage": [], "errors": []}}
            TUNING_ROWS = json.loads({_json_string(_tuning_rows())})
            AUTO_ENTRY_REPORT = {{
                "events": [],
                "latest_context": {{}},
                "rejection_summary": [],
                "symbol_reject_summary": [],
                "symbol_candidate_summary": [],
                "top_candidates": [],
            }}

            pages._cached_coin_ranking = lambda **kwargs: RANKING_PAYLOAD
            pages.build_live_rule_tuning_rows = lambda **kwargs: TUNING_ROWS
            pages.fetch_open_execution_orders = lambda: [{{"symbol": "THB_SUMX", "side": "buy", "state": "open", "request_payload": {{"amt": 100.0}}}}]
            pages.insert_runtime_event = lambda **kwargs: None
            pages._build_auto_entry_review_report = lambda limit=40: AUTO_ENTRY_REPORT
            pages.build_symbol_operational_state = lambda **kwargs: (
                {{
                    "symbol": "THB_SUMX",
                    "state_summary": "open buy 1 | open sell 0 | reserved THB 100.00 | reserved coin 1.00000000",
                    "risk_summary": "entry blocked | exit clear",
                    "available_coin": 1.0,
                    "reserved_coin": 1.0,
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
                    "recent_guardrail_block": None,
                    "holdings_row": {{}},
                    "exchange_open_order_count": 1,
                    "exchange_open_orders": [],
                    "local_open_orders": [{{"symbol": "THB_SUMX"}}],
                    "findings": {{}},
                    "symbol_findings": {{}},
                }}
                if str(kwargs["symbol"]) == "THB_SUMX"
                else {{
                    "symbol": str(kwargs["symbol"]),
                    "state_summary": "open buy 0 | open sell 0 | reserved THB 0.00 | reserved coin 0.00000000",
                    "risk_summary": "entry clear | exit clear",
                    "available_coin": 0.0,
                    "reserved_coin": 0.0,
                    "reserved_thb": 0.0,
                    "open_buy_count": 0,
                    "open_sell_count": 0,
                    "partial_fill": False,
                    "entry_blocked": False,
                    "exit_blocked": False,
                    "entry_block_reasons": [],
                    "exit_block_reasons": [],
                    "review_required": False,
                    "review_reasons": [],
                    "recent_guardrail_block": None,
                    "holdings_row": {{}},
                    "exchange_open_order_count": 0,
                    "exchange_open_orders": [],
                    "local_open_orders": [],
                    "findings": {{}},
                    "symbol_findings": {{}},
                }}
            )
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        prune_action = at.radio(key="strategy_prune_live_rules_action")
        self.assertIn("Prune rule only", list(prune_action.options))
        self.assertIn("Cancel linked orders and prune", list(prune_action.options))
        self.assertIn("Review in Live Ops", list(prune_action.options))

    def test_live_tuning_prune_routes_to_live_ops_when_state_is_unclear(self) -> None:
        script = _app_script(
            workspace="Live Tuning",
            body=f"""
            RANKING_PAYLOAD = {{"rows": [], "coverage": [], "errors": []}}
            TUNING_ROWS = json.loads({_json_string(_tuning_rows())})
            AUTO_ENTRY_REPORT = {{
                "events": [],
                "latest_context": {{}},
                "rejection_summary": [],
                "symbol_reject_summary": [],
                "symbol_candidate_summary": [],
                "top_candidates": [],
            }}

            pages._cached_coin_ranking = lambda **kwargs: RANKING_PAYLOAD
            pages.build_live_rule_tuning_rows = lambda **kwargs: TUNING_ROWS
            pages.fetch_open_execution_orders = lambda: [{{"id": 9, "symbol": "THB_SUMX", "side": "buy", "state": "open", "request_payload": {{"amt": 100.0}}}}]
            pages.insert_runtime_event = lambda **kwargs: None
            pages._build_auto_entry_review_report = lambda limit=40: AUTO_ENTRY_REPORT

            def save_capture(*args, **kwargs):
                st.session_state["prune_saved"] = True
                return True

            pages.save_config_with_feedback = save_capture
            pages.build_symbol_operational_state = lambda **kwargs: (
                {{
                    "symbol": "THB_SUMX",
                    "state_summary": "open buy 1 | open sell 0 | reserved THB 100.00 | reserved coin 0.00000000",
                    "risk_summary": "entry blocked | exit clear",
                    "available_coin": 0.0,
                    "reserved_coin": 0.0,
                    "reserved_thb": 100.0,
                    "open_buy_count": 1,
                    "open_sell_count": 0,
                    "partial_fill": False,
                    "entry_blocked": True,
                    "exit_blocked": False,
                    "entry_block_reasons": ["1 open buy order(s) exist"],
                    "exit_block_reasons": [],
                    "review_required": True,
                    "review_reasons": ["exchange open-orders coverage is partial"],
                    "recent_guardrail_block": None,
                    "holdings_row": {{}},
                    "exchange_open_order_count": 0,
                    "exchange_open_orders": [],
                    "local_open_orders": [{{"symbol": "THB_SUMX"}}],
                    "findings": {{}},
                    "symbol_findings": {{}},
                }}
                if str(kwargs["symbol"]) == "THB_SUMX"
                else {{
                    "symbol": str(kwargs["symbol"]),
                    "state_summary": "open buy 0 | open sell 0 | reserved THB 0.00 | reserved coin 0.00000000",
                    "risk_summary": "entry clear | exit clear",
                    "available_coin": 0.0,
                    "reserved_coin": 0.0,
                    "reserved_thb": 0.0,
                    "open_buy_count": 0,
                    "open_sell_count": 0,
                    "partial_fill": False,
                    "entry_blocked": False,
                    "exit_blocked": False,
                    "entry_block_reasons": [],
                    "exit_block_reasons": [],
                    "review_required": False,
                    "review_reasons": [],
                    "recent_guardrail_block": None,
                    "holdings_row": {{}},
                    "exchange_open_order_count": 0,
                    "exchange_open_orders": [],
                    "local_open_orders": [],
                    "findings": {{}},
                    "symbol_findings": {{}},
                }}
            )
            """,
        )

        at = AppTest.from_string(script)
        at.run(timeout=20)

        at.multiselect(key="strategy_prune_live_rules_selection").set_value(["THB_SUMX"])
        at.run(timeout=20)

        prune_action = at.radio(key="strategy_prune_live_rules_action")
        self.assertEqual(list(prune_action.options), ["Review in Live Ops"])

        next(button for button in at.button if button.label == "Continue").click()
        at.run(timeout=20)

        self.assertEqual(len(at.exception), 0)
        self.assertNotIn("prune_saved", at.session_state)
        self.assertEqual(at.session_state["ui_page_autorun"], "Live Ops")
        self.assertEqual(at.session_state["live_ops_focus_symbol"], "THB_SUMX")


if __name__ == "__main__":
    unittest.main()
