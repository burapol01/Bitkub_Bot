from __future__ import annotations

from typing import Any

import streamlit as st

from config import CONFIG_BASE_PATH, CONFIG_PATH, save_config, summarize_config_changes
from services.telegram_service import DEFAULT_TELEGRAM_NOTIFY_EVENTS
from ui.streamlit.strategy_support import build_rule_seed, fetch_market_symbol_universe
from ui.streamlit.styles import badge, render_metric_card


def _sync_form_defaults(*, prefix: str, values: dict[str, Any]) -> None:
    signature_key = f"{prefix}__signature"
    signature = tuple((key, values[key]) for key in sorted(values))
    if st.session_state.get(signature_key) == signature:
        return

    for key, value in values.items():
        st.session_state[f"{prefix}_{key}"] = value
    st.session_state[signature_key] = signature


def _show_config_save_feedback() -> None:
    summary_lines = st.session_state.get("config_save_summary")
    summary_title = st.session_state.get("config_save_title")
    if not summary_lines:
        return

    st.success(summary_title or "Saved config.json")
    with st.expander("Applied Config Changes", expanded=True):
        for line in summary_lines:
            st.write(f"- {line}")



def save_config_with_feedback(
    current_config: dict[str, Any],
    updated_config: dict[str, Any],
    success_title: str,
) -> bool:
    _, errors = save_config(updated_config)
    if errors:
        for error in errors:
            st.error(error)
        return False

    st.session_state["config_save_title"] = success_title
    st.session_state["config_save_summary"] = summarize_config_changes(
        current_config,
        updated_config,
    )
    return True



def render_config_page(*, config: dict[str, Any]) -> None:
    st.markdown('<div class="panel-title">Config Editor</div>', unsafe_allow_html=True)
    if CONFIG_BASE_PATH.exists() and CONFIG_BASE_PATH != CONFIG_PATH:
        st.caption(
            f"Merged config: base=`{CONFIG_BASE_PATH}` + override=`{CONFIG_PATH}`"
        )
    else:
        st.caption(f"Source of truth: `{CONFIG_PATH}`")
    _show_config_save_feedback()

    market_universe = fetch_market_symbol_universe()
    configured_symbols = sorted(config["rules"].keys())
    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", configured_symbols)
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    market_symbols = list(market_universe.get("symbols", []))
    available_new_symbols = [symbol for symbol in market_symbols if symbol not in config["rules"]]

    st.markdown(
        """
        <div class="note-strip">
          <strong>Apply Model</strong><br>
          Changes saved here write to the active override file only.
          The merged result still uses the base config underneath, and the console engine remains the runner so it still needs its own reload/apply step.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    with summary_col1:
        render_metric_card("Mode", str(config["mode"]).upper(), f"Base URL {config['base_url']}")
    with summary_col2:
        render_metric_card(
            "Watchlist",
            str(len(watchlist_symbols)),
            f"Live rules {len(config['rules'])} | Market {len(market_symbols) if market_symbols else 'n/a'}",
        )
    with summary_col3:
        render_metric_card(
            "Live Controls",
            "ON" if bool(config["live_execution_enabled"]) else "OFF",
            (
                "Auto entry ON" if bool(config.get("live_auto_entry_enabled", False)) else "Auto entry OFF"
            ) + " | " + (
                "Auto exit ON" if bool(config.get("live_auto_exit_enabled", False)) else "Auto exit OFF"
            ),
        )
    with summary_col4:
        render_metric_card(
            "Manual Preset",
            str(config.get("live_manual_order", {}).get("symbol", "n/a")),
            str(config.get("live_manual_order", {}).get("side", "n/a")).upper(),
        )

    st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
    with st.expander("Live Rule Summary", expanded=False):
        st.dataframe(
            [
                {
                    "symbol": symbol,
                    "buy_below": float(rule["buy_below"]),
                    "sell_above": float(rule["sell_above"]),
                    "budget_thb": float(rule["budget_thb"]),
                    "stop_loss_percent": float(rule["stop_loss_percent"]),
                    "take_profit_percent": float(rule["take_profit_percent"]),
                    "max_trades_per_day": int(rule["max_trades_per_day"]),
                }
                for symbol, rule in sorted(config["rules"].items())
            ],
            width='stretch',
            hide_index=True,
        )

    left, right = st.columns([1, 1])

    with left:
        st.markdown("#### System Settings")
        st.caption("These fields shape the console engine behavior after it reloads config.")
        modes = ["paper", "read-only", "live-disabled", "live"]
        rank_resolution_options = ["1", "5", "15", "60", "240", "1D"]
        current_rank_resolution = str(config.get("live_auto_entry_rank_resolution", "240"))
        if current_rank_resolution not in {"1", "5", "15", "60", "240", "1D"}:
            current_rank_resolution = "240"
        current_biases = [
            bias
            for bias in list(config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"]))
            if bias in {"bullish", "mixed", "weak"}
        ] or ["bullish", "mixed"]
        _sync_form_defaults(
            prefix="config_system",
            values={
                "mode": str(config["mode"]),
                "base_url": str(config["base_url"]),
                "fee_rate": float(config["fee_rate"]),
                "interval_seconds": int(config["interval_seconds"]),
                "cooldown_seconds": int(config["cooldown_seconds"]),
                "live_execution_enabled": bool(config["live_execution_enabled"]),
                "live_auto_entry_enabled": bool(config.get("live_auto_entry_enabled", False)),
                "live_auto_exit_enabled": bool(config.get("live_auto_exit_enabled", False)),
                "live_auto_entry_require_ranking": bool(config.get("live_auto_entry_require_ranking", True)),
                "live_auto_entry_rank_resolution": current_rank_resolution,
                "live_auto_entry_rank_lookback_days": int(config.get("live_auto_entry_rank_lookback_days", 14)),
                "live_auto_entry_min_score": float(config.get("live_auto_entry_min_score", 50.0)),
                "live_auto_entry_allowed_biases": list(current_biases),
                "live_max_order_thb": float(config["live_max_order_thb"]),
                "live_min_thb_balance": float(config["live_min_thb_balance"]),
                "live_slippage_tolerance_percent": float(config["live_slippage_tolerance_percent"]),
                "live_daily_loss_limit_thb": float(config["live_daily_loss_limit_thb"]),
            },
        )
        with st.form("config_system_form"):
            mode = st.selectbox("Mode", modes, key="config_system_mode")
            base_url = st.text_input("Base URL", key="config_system_base_url")
            fee_rate = st.number_input(
                "Fee Rate",
                min_value=0.0,
                max_value=0.9999,
                format="%.6f",
                key="config_system_fee_rate",
            )
            interval_seconds = st.number_input(
                "Interval Seconds",
                min_value=1,
                step=1,
                key="config_system_interval_seconds",
            )
            cooldown_seconds = st.number_input(
                "Cooldown Seconds",
                min_value=0,
                step=1,
                key="config_system_cooldown_seconds",
            )
            live_execution_enabled = st.checkbox(
                "Live Execution Enabled",
                key="config_system_live_execution_enabled",
            )
            live_auto_entry_enabled = st.checkbox(
                "Live Auto Entry Enabled",
                key="config_system_live_auto_entry_enabled",
            )
            live_auto_exit_enabled = st.checkbox(
                "Live Auto Exit Enabled",
                key="config_system_live_auto_exit_enabled",
            )
            live_auto_entry_require_ranking = st.checkbox(
                "Auto Entry Require Ranking",
                key="config_system_live_auto_entry_require_ranking",
            )
            live_auto_entry_rank_resolution = st.selectbox(
                "Auto Entry Rank Resolution",
                rank_resolution_options,
                key="config_system_live_auto_entry_rank_resolution",
            )
            live_auto_entry_rank_lookback_days = st.number_input(
                "Auto Entry Rank Lookback Days",
                min_value=1,
                step=1,
                key="config_system_live_auto_entry_rank_lookback_days",
            )
            live_auto_entry_min_score = st.number_input(
                "Auto Entry Minimum Score",
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                key="config_system_live_auto_entry_min_score",
            )
            live_auto_entry_allowed_biases = st.multiselect(
                "Auto Entry Allowed Biases",
                ["bullish", "mixed", "weak"],
                key="config_system_live_auto_entry_allowed_biases",
            )
            live_max_order_thb = st.number_input(
                "Live Max Order THB",
                min_value=1.0,
                step=10.0,
                key="config_system_live_max_order_thb",
            )
            live_min_thb_balance = st.number_input(
                "Live Min THB Balance",
                min_value=0.0,
                step=10.0,
                key="config_system_live_min_thb_balance",
            )
            live_slippage_tolerance_percent = st.number_input(
                "Live Slippage Tolerance %",
                min_value=0.01,
                format="%.2f",
                key="config_system_live_slippage_tolerance_percent",
            )
            live_daily_loss_limit_thb = st.number_input(
                "Live Daily Loss Limit THB",
                min_value=1.0,
                step=50.0,
                key="config_system_live_daily_loss_limit_thb",
            )
            submitted_system = st.form_submit_button("Save System Settings", width='stretch')

        if submitted_system:
            updated = dict(config)
            updated.update(
                {
                    "mode": mode,
                    "base_url": base_url,
                    "fee_rate": float(fee_rate),
                    "interval_seconds": int(interval_seconds),
                    "cooldown_seconds": int(cooldown_seconds),
                    "live_execution_enabled": bool(live_execution_enabled),
                    "live_auto_entry_enabled": bool(live_auto_entry_enabled),
                    "live_auto_exit_enabled": bool(live_auto_exit_enabled),
                    "live_auto_entry_require_ranking": bool(live_auto_entry_require_ranking),
                    "live_auto_entry_rank_resolution": str(live_auto_entry_rank_resolution),
                    "live_auto_entry_rank_lookback_days": int(live_auto_entry_rank_lookback_days),
                    "live_auto_entry_min_score": float(live_auto_entry_min_score),
                    "live_auto_entry_allowed_biases": list(live_auto_entry_allowed_biases) or ["bullish", "mixed"],
                    "live_max_order_thb": float(live_max_order_thb),
                    "live_min_thb_balance": float(live_min_thb_balance),
                    "live_slippage_tolerance_percent": float(live_slippage_tolerance_percent),
                    "live_daily_loss_limit_thb": float(live_daily_loss_limit_thb),
                }
            )
            if save_config_with_feedback(config, updated, "Saved system settings to config.json"):
                st.rerun()

        st.markdown("#### Retention")
        st.caption("SQLite cleanup policy for stored snapshots, logs, and reconciliation records.")
        with st.form("config_retention_form"):
            market_snapshot_retention_days = st.number_input("Market Snapshots Retention", min_value=1, value=int(config["market_snapshot_retention_days"]), step=1)
            signal_log_retention_days = st.number_input("Signal Logs Retention", min_value=1, value=int(config["signal_log_retention_days"]), step=1)
            runtime_event_retention_days = st.number_input("Runtime Events Retention", min_value=1, value=int(config["runtime_event_retention_days"]), step=1)
            account_snapshot_retention_days = st.number_input("Account Snapshots Retention", min_value=1, value=int(config["account_snapshot_retention_days"]), step=1)
            reconciliation_retention_days = st.number_input("Reconciliation Retention", min_value=1, value=int(config["reconciliation_retention_days"]), step=1)
            submitted_retention = st.form_submit_button("Save Retention", width='stretch')
        if submitted_retention:
            updated = dict(config)
            updated.update(
                {
                    "market_snapshot_retention_days": int(market_snapshot_retention_days),
                    "signal_log_retention_days": int(signal_log_retention_days),
                    "runtime_event_retention_days": int(runtime_event_retention_days),
                    "account_snapshot_retention_days": int(account_snapshot_retention_days),
                    "reconciliation_retention_days": int(reconciliation_retention_days),
                }
            )
            if save_config_with_feedback(config, updated, "Saved retention settings to config.json"):
                st.rerun()

    with right:
        st.markdown("#### Watchlist")
        st.caption("Watchlist symbols drive research, candle sync, and ranking. Live auto-entry still uses config rules only.")
        watchlist_options = market_symbols or sorted(set(watchlist_symbols) | set(configured_symbols))
        with st.form("config_watchlist_form"):
            selected_watchlist = st.multiselect(
                "Watchlist Symbols",
                watchlist_options,
                default=[symbol for symbol in watchlist_symbols if symbol in watchlist_options],
                help="Use this to keep a wider research universe than the live tradable shortlist.",
            )
            watchlist_fallback = st.text_input(
                "Add Watchlist Symbols (comma-separated fallback)",
                value="",
                help="Only needed when the Bitkub market universe is unavailable or you want to paste symbols quickly.",
            )
            submitted_watchlist = st.form_submit_button("Save Watchlist", width='stretch')
        if submitted_watchlist:
            extra_symbols = [
                entry.strip().upper()
                for entry in str(watchlist_fallback).split(",")
                if entry.strip()
            ]
            updated = dict(config)
            updated["watchlist_symbols"] = sorted(set(selected_watchlist) | set(extra_symbols) | set(configured_symbols))
            if save_config_with_feedback(config, updated, "Saved watchlist symbols"):
                st.rerun()

        st.markdown("#### Telegram Foundation")
        st.caption("Telegram notifications use TELEGRAM_BOT_TOKEN plus TELEGRAM_CHAT_IDS or TELEGRAM_CHAT_ID from .env. Telegram commands are allowed only for TELEGRAM_ALLOWED_CHAT_IDS when set; otherwise they fall back to the notify chat ids.")
        telegram_notify_defaults = [
            event_name
            for event_name in config.get("telegram_notify_events", DEFAULT_TELEGRAM_NOTIFY_EVENTS)
            if event_name in DEFAULT_TELEGRAM_NOTIFY_EVENTS
        ]
        with st.form("config_telegram_form"):
            telegram_enabled = st.checkbox("Telegram Notifications Enabled", value=bool(config.get("telegram_enabled", False)))
            telegram_control_enabled = st.checkbox("Telegram Control Enabled", value=bool(config.get("telegram_control_enabled", False)))
            telegram_notify_events = st.multiselect(
                "Notify Events",
                DEFAULT_TELEGRAM_NOTIFY_EVENTS,
                default=telegram_notify_defaults or DEFAULT_TELEGRAM_NOTIFY_EVENTS,
            )
            submitted_telegram = st.form_submit_button("Save Telegram Settings", width='stretch')
        if submitted_telegram:
            updated = dict(config)
            updated["telegram_enabled"] = bool(telegram_enabled)
            updated["telegram_control_enabled"] = bool(telegram_control_enabled)
            updated["telegram_notify_events"] = [str(event_name) for event_name in telegram_notify_events]
            if save_config_with_feedback(config, updated, "Saved Telegram foundation settings"):
                st.rerun()

        st.markdown("#### Manual Live Order Preset")
        st.caption("This preset is used by manual live execution actions, not by the auto loop.")
        symbols = configured_symbols
        manual_order = dict(config.get("live_manual_order", {}))
        default_symbol = str(manual_order.get("symbol", symbols[0]))
        with st.form("config_manual_order_form"):
            mo_enabled = st.checkbox("Enabled", value=bool(manual_order.get("enabled", False)))
            mo_symbol = st.selectbox(
                "Symbol",
                symbols,
                index=max(0, symbols.index(default_symbol)) if default_symbol in symbols else 0,
            )
            mo_side = st.selectbox("Side", ["buy", "sell"], index=0 if manual_order.get("side", "buy") == "buy" else 1)
            mo_order_type = st.selectbox("Order Type", ["limit"], index=0)
            mo_amount_thb = st.number_input("Amount THB", min_value=0.01, value=float(manual_order.get("amount_thb", 100.0)), step=10.0)
            mo_amount_coin = st.number_input("Amount Coin", min_value=0.00000001, value=float(manual_order.get("amount_coin", 0.0001)), format="%.8f")
            mo_rate = st.number_input("Rate", min_value=0.00000001, value=float(manual_order.get("rate", 1.0)), format="%.8f")
            submitted_manual_order = st.form_submit_button("Save Manual Order Preset", width='stretch')
        if submitted_manual_order:
            updated = dict(config)
            updated["live_manual_order"] = {
                "enabled": bool(mo_enabled),
                "symbol": mo_symbol,
                "side": mo_side,
                "order_type": mo_order_type,
                "amount_thb": float(mo_amount_thb),
                "amount_coin": float(mo_amount_coin),
                "rate": float(mo_rate),
            }
            if save_config_with_feedback(config, updated, "Saved manual live order preset"):
                st.rerun()

        st.markdown("#### Rules Editor")
        st.caption("Edit one symbol at a time. Changes are saved back into the shared config file.")
        selected_rule_symbol = st.selectbox("Rule Symbol", symbols, key="selected_rule_symbol")
        rule = dict(config["rules"][selected_rule_symbol])
        st.markdown(
            " ".join(
                [
                    badge(f"buy <= {float(rule['buy_below']):,.8f}", "info"),
                    badge(f"sell >= {float(rule['sell_above']):,.8f}", "good"),
                    badge(f"budget {float(rule['budget_thb']):,.2f} THB", "warn"),
                ]
            ),
            unsafe_allow_html=True,
        )
        with st.form("config_rule_editor_form"):
            buy_below = st.number_input("Buy Below", min_value=0.00000001, value=float(rule["buy_below"]), format="%.8f")
            sell_above = st.number_input("Sell Above", min_value=0.00000001, value=float(rule["sell_above"]), format="%.8f")
            budget_thb = st.number_input("Budget THB", min_value=0.01, value=float(rule["budget_thb"]), step=10.0)
            stop_loss_percent = st.number_input("Stop Loss %", min_value=0.01, value=float(rule["stop_loss_percent"]), format="%.4f")
            take_profit_percent = st.number_input("Take Profit %", min_value=0.01, value=float(rule["take_profit_percent"]), format="%.4f")
            max_trades_per_day = st.number_input("Max Trades Per Day", min_value=1, value=int(rule["max_trades_per_day"]), step=1)
            submitted_rule = st.form_submit_button("Save Rule", width='stretch')
        if submitted_rule:
            updated = dict(config)
            updated_rules = dict(config["rules"])
            updated_rules[selected_rule_symbol] = {
                "buy_below": float(buy_below),
                "sell_above": float(sell_above),
                "budget_thb": float(budget_thb),
                "stop_loss_percent": float(stop_loss_percent),
                "take_profit_percent": float(take_profit_percent),
                "max_trades_per_day": int(max_trades_per_day),
            }
            updated["rules"] = updated_rules
            if save_config_with_feedback(config, updated, f"Saved rule for {selected_rule_symbol}"):
                st.rerun()

        st.markdown("#### Add / Remove Rule")
        st.caption("Use this section only for symbol lifecycle changes. It is riskier than normal rule edits.")
        if market_universe.get("error"):
            st.warning(f"Bitkub market symbols unavailable right now: {market_universe['error']}")
            new_symbol = st.text_input(
                "New Symbol",
                value="",
                help="Fallback manual entry when the Bitkub market-symbol endpoint is unavailable.",
            ).strip().upper()
        elif market_symbols:
            st.caption(
                f"Bitkub market symbols loaded: {len(market_symbols)} | already configured: {len(configured_symbols)} | available to add: {len(available_new_symbols)}"
            )
            if available_new_symbols:
                new_symbol = st.selectbox(
                    "New Symbol",
                    available_new_symbols,
                    help="Symbols already configured are hidden from this list.",
                    key="config_new_rule_symbol",
                )
            else:
                new_symbol = ""
                st.info("All currently loaded Bitkub market symbols are already present in bot rules.")
        else:
            new_symbol = ""
            st.info("No Bitkub market symbols were returned right now.")
        add_col, remove_col = st.columns(2)
        with add_col:
            if st.button("Add New Rule", width='stretch'):
                if not new_symbol:
                    st.error("No market symbol is available to add right now.")
                elif new_symbol in config["rules"]:
                    st.error("That symbol already exists in rules.")
                else:
                    updated = dict(config)
                    updated_rules = dict(config["rules"])
                    updated_rules[new_symbol] = build_rule_seed(config, new_symbol)
                    updated["rules"] = updated_rules
                    updated["watchlist_symbols"] = sorted(set(config.get("watchlist_symbols", configured_symbols)) | {new_symbol})
                    if save_config_with_feedback(config, updated, f"Added new rule {new_symbol}"):
                        st.rerun()
        with remove_col:
            confirm_remove = st.checkbox("Confirm remove selected rule", key="confirm_remove_rule")
            if st.button("Remove Selected Rule", width='stretch'):
                if not confirm_remove:
                    st.error("Tick confirm before removing a rule.")
                elif len(config["rules"]) <= 1:
                    st.error("At least one rule must remain in config.")
                else:
                    updated = dict(config)
                    updated_rules = dict(config["rules"])
                    updated_rules.pop(selected_rule_symbol, None)
                    updated["rules"] = updated_rules
                    updated["watchlist_symbols"] = sorted(set(config.get("watchlist_symbols", [])) | set(updated_rules.keys()))
                    if save_config_with_feedback(config, updated, f"Removed rule {selected_rule_symbol}"):
                        st.rerun()

    with st.expander("Raw Config Preview", expanded=False):
        st.json(config, expanded=False)
