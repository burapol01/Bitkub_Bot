from __future__ import annotations

import json
import textwrap
import unittest

from streamlit.testing.v1 import AppTest


def _json_string(value: object) -> str:
    return repr(json.dumps(value, separators=(",", ":"), ensure_ascii=True))


def _base_config() -> dict[str, object]:
    return {
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
        "market_snapshot_retention_days": 30,
        "live_auto_entry_min_score": 50.0,
        "live_auto_entry_allowed_biases": ["bullish", "mixed"],
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


def _app_script(*, workspace: str, body: str) -> str:
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
            "init_db()",
            "",
            textwrap.dedent(body).strip(),
            "",
            f"st.session_state['strategy_workspace'] = {workspace!r}",
            "pages.render_strategy_page(config=CONFIG)",
            "",
        ]
    )


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
        at.run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        prune_widget = at.multiselect(key="strategy_prune_live_rules_selection")
        self.assertEqual(list(prune_widget.options), ["THB_SUMX"])
        self.assertEqual(list(prune_widget.value), ["THB_SUMX"])

        submit_button = next(
            button for button in at.button if button.label == "Prune Selected Live Rules"
        )
        submit_button.click()
        at.run(timeout=10)

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
        at.run(timeout=10)

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
        at.run(timeout=10)

        at.selectbox(key="strategy_compare_symbol__input").set_value("THB_SUMX")
        next(button for button in at.button if button.label == "Run Compare").click()
        at.run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.selectbox(key="strategy_compare_symbol__input").value, "THB_SUMX")
        rendered = "\n".join(str(markdown.value) for markdown in at.markdown)
        self.assertIn("Symbol THB_SUMX", rendered)


if __name__ == "__main__":
    unittest.main()
