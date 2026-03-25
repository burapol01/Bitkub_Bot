from __future__ import annotations

from collections import defaultdict
from typing import Any

import streamlit as st

from config import CONFIG_PATH

from services.db_service import (
    DB_PATH,
    fetch_reporting_summary,
    insert_runtime_event,
)
from services.strategy_lab_service import (
    build_coin_ranking,
    fetch_market_snapshot_coverage,
    fetch_trade_analytics,
    run_market_candle_replay,
    run_market_snapshot_replay,
    sync_candles_for_symbols,
)
from ui.streamlit.config_support import render_config_page
from ui.streamlit.ops_pages import render_account_page, render_live_ops_page, render_overview_page
from ui.streamlit.diagnostics_support import render_diagnostics_page, render_logs_page
from ui.streamlit.data import calc_daily_totals
from ui.streamlit.refresh import PAGE_ORDER, render_refreshable_fragment
from ui.streamlit.styles import badge, render_metric_card
from ui.streamlit.strategy_support import (
    annotate_strategy_compare_rows,
    build_live_rule_tuning_rows,
    build_rule_compare_variants,
    build_rule_seed,
    fetch_market_symbol_universe,
    run_strategy_compare_rows,
)


def _summarize_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, int] = defaultdict(int)
    for line in lines:
        grouped[str(line)] += 1
    return [
        {"message": message, "count": count}
        for message, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    ]


def render_sidebar(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any],
    selected_page: str,
) -> str:
    with st.sidebar:
        st.markdown("### Navigation")
        page_name = st.radio(
            "Page",
            PAGE_ORDER,
            index=PAGE_ORDER.index(selected_page),
            label_visibility="collapsed",
        )
        st.markdown("### Control")
        st.caption(f"Config: `{CONFIG_PATH}`")
        st.caption(f"SQLite: `{DB_PATH}`")
        if st.button("Refresh Dashboard", width='stretch'):
            st.rerun()
        st.markdown("### Status")
        st.markdown(badge(f"Mode {str(config['mode']).upper()}", "info"), unsafe_allow_html=True)
        for item in private_ctx["private_api_capabilities"]:
            tone = "good" if item.endswith("=OK") else "warn" if item.endswith("=PARTIAL") else "bad"
            st.markdown(badge(item, tone), unsafe_allow_html=True)
        st.caption(private_ctx["private_api_status"])
    return page_name


def render_strategy_page(*, config: dict[str, Any]) -> None:
    st.markdown('<div class="panel-title">Strategy Lab</div>', unsafe_allow_html=True)
    st.caption(
        "Use actual paper-trade logs, stored market snapshots, and stored candles to evaluate whether the current rule set has edge. "
        "Watchlist symbols act as the research universe, while config rules remain the live trading shortlist."
    )

    st.markdown('<div class="panel-title">Strategy Workflow</div>', unsafe_allow_html=True)
    workflow_cols = st.columns(4)
    with workflow_cols[0]:
        render_metric_card("1. Sync & Rank", "Universe first", "Sync candles, then use Coin Ranking to see which symbols deserve attention.")
    with workflow_cols[1]:
        render_metric_card("2. Shortlist", "Gate live rules", "Check Auto Entry Shortlist and confirm which current rules still pass ranking filters.")
    with workflow_cols[2]:
        render_metric_card("3. Tune", "Keep / Review / Prune", "Use Live Rule Tuning to identify weak live rules before widening automation.")
    with workflow_cols[3]:
        render_metric_card("4. Compare", "Beat baseline", "Use Strategy Compare Lab to test variants, then apply the winner back into config.")
    st.caption(
        "Recommended order: Coin Ranking -> Auto Entry Shortlist -> Live Rule Tuning -> Strategy Compare Lab -> Replay Lab for manual deep-dive."
    )

    analytics = fetch_trade_analytics()
    totals = analytics["totals"]
    coverage_days = int(config.get("market_snapshot_retention_days", 30))
    coverage_rows = fetch_market_snapshot_coverage(days=coverage_days)

    card1, card2, card3, card4 = st.columns(4)
    with card1:
        render_metric_card("Actual Trades", str(totals["trades"]), f"Win rate {totals['win_rate_percent']:.2f}%")
    with card2:
        render_metric_card("Actual PnL", f"{totals['total_pnl_thb']:,.2f} THB", f"Avg/trade {totals['avg_pnl_thb']:,.2f}")
    with card3:
        render_metric_card("Profit Factor", f"{totals['profit_factor']:.2f}", f"Avg win {totals['avg_win_thb']:,.2f}")
    with card4:
        render_metric_card("Hold Time", f"{totals['avg_hold_minutes']:.1f} min", f"Losses {totals['losses']}")

    top_left, top_right = st.columns([1.15, 0.85])
    with top_left:
        st.markdown('<div class="panel-title">Actual Trade Analytics by Symbol</div>', unsafe_allow_html=True)
        if analytics["by_symbol"]:
            st.dataframe(analytics["by_symbol"], width='stretch', hide_index=True)
        else:
            st.caption("No paper trade history exists yet. Run the paper bot longer or import prior trade logs first.")
    with top_right:
        st.markdown('<div class="panel-title">Exit Reason Breakdown</div>', unsafe_allow_html=True)
        if analytics["by_exit_reason"]:
            st.dataframe(analytics["by_exit_reason"], width='stretch', hide_index=True)
        else:
            st.caption("No exit reasons are available because no paper trades have been stored yet.")

    with st.expander("Recent Actual Paper Trades", expanded=False):
        if analytics["recent_trades"]:
            st.dataframe(analytics["recent_trades"], width='stretch', hide_index=True)
        else:
            st.caption("No recent paper trades available.")

    st.markdown('<div class="panel-title">Candle Sync & Coin Ranking</div>', unsafe_allow_html=True)
    st.caption(
        "Sync TradingView history into SQLite first, then rank coins by recent momentum, range position, stability, and average volume."
    )

    market_universe = fetch_market_symbol_universe()
    configured_symbols = sorted(config["rules"].keys())
    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", configured_symbols)
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    market_symbols = list(market_universe.get("symbols", []))
    symbols = market_symbols or watchlist_symbols or configured_symbols
    if market_universe.get("error"):
        st.warning(f"Bitkub market symbols unavailable right now: {market_universe['error']}")
    if market_symbols:
        st.caption(
            f"Market universe loaded: {len(market_symbols)} symbol(s) | watchlist: {len(watchlist_symbols)} | live rules: {len(configured_symbols)}"
        )
    elif watchlist_symbols or configured_symbols:
        st.caption(
            f"Using local symbol list only | watchlist: {len(watchlist_symbols)} | live rules: {len(configured_symbols)}"
        )

    rank_resolution_options = ["1", "5", "15", "60", "240", "1D"]
    default_rank_resolution = str(st.session_state.get("strategy_rank_resolution", "240"))
    if default_rank_resolution not in rank_resolution_options:
        default_rank_resolution = "240"
    default_rank_days = int(st.session_state.get("strategy_rank_days", 14))

    with st.form("strategy_candle_sync_form"):
        sync_col1, sync_col2, sync_col3 = st.columns([0.4, 0.3, 0.3])
        with sync_col1:
            selected_sync_symbols = st.multiselect(
                "Symbols to Sync",
                symbols,
                default=watchlist_symbols or configured_symbols or symbols[: min(len(symbols), 10)],
                help="Options come from the full Bitkub market universe when available. The default selection still follows the watchlist.",
            )
        with sync_col2:
            ranking_resolution = st.selectbox(
                "Candle Resolution",
                rank_resolution_options,
                index=rank_resolution_options.index(default_rank_resolution),
            )
        with sync_col3:
            ranking_days = st.number_input(
                "Lookback Days",
                min_value=1,
                max_value=90,
                value=default_rank_days,
                step=1,
            )
        run_candle_sync = st.form_submit_button("Sync Candles", type="primary", width='stretch')

    if run_candle_sync:
        sync_result = sync_candles_for_symbols(
            symbols=selected_sync_symbols or symbols,
            resolution=str(ranking_resolution),
            days=int(ranking_days),
        )
        st.session_state["strategy_candle_sync_result"] = sync_result
        st.session_state["strategy_rank_resolution"] = str(ranking_resolution)
        st.session_state["strategy_rank_days"] = int(ranking_days)

    sync_feedback = st.session_state.get("strategy_candle_sync_result")
    if sync_feedback:
        if sync_feedback.get("synced"):
            st.success(
                f"Synced candles for {len(sync_feedback['synced'])} symbol(s) | resolution={sync_feedback['resolution']} | days={sync_feedback['days']}"
            )
            with st.expander("Sync Result Details", expanded=False):
                st.dataframe(sync_feedback["synced"], width='stretch', hide_index=True)
        if sync_feedback.get("errors"):
            summarized_sync_errors = _summarize_text_lines(list(sync_feedback["errors"]))
            no_data_count = sum(row["count"] for row in summarized_sync_errors if "history status=no_data" in str(row["message"]))
            st.warning(
                f"Sync warnings: {len(sync_feedback['errors'])} total | no_data {no_data_count}"
            )
            with st.expander("Sync Warning Summary", expanded=False):
                st.dataframe(summarized_sync_errors, width='stretch', hide_index=True)

    ranking_resolution = str(st.session_state.get("strategy_rank_resolution", default_rank_resolution))
    ranking_days = int(st.session_state.get("strategy_rank_days", default_rank_days))
    ranking = build_coin_ranking(
        symbols=symbols,
        resolution=ranking_resolution,
        lookback_days=ranking_days,
    )

    rank_card1, rank_card2, rank_card3 = st.columns(3)
    with rank_card1:
        render_metric_card("Ranking Resolution", ranking_resolution, f"Lookback {ranking_days} day(s)")
    with rank_card2:
        render_metric_card("Ranked Symbols", str(len(ranking["rows"])), f"Coverage rows {len(ranking['coverage'])}")
    with rank_card3:
        top_score = ranking["rows"][0]["score"] if ranking["rows"] else 0.0
        render_metric_card("Top Score", f"{top_score:.2f}", "Higher = stronger trend shortlist")

    auto_entry_min_score = float(config.get("live_auto_entry_min_score", 50.0))
    auto_entry_allowed_biases = {
        str(value).strip().lower()
        for value in config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"])
        if str(value).strip()
    } or {"bullish", "mixed"}
    shortlist_rows = []
    for row in ranking["rows"]:
        row_bias = str(row.get("trend_bias") or "").lower()
        passes = float(row.get("score", 0.0) or 0.0) >= auto_entry_min_score and row_bias in auto_entry_allowed_biases
        in_live_rules = row["symbol"] in config["rules"]
        shortlist_rows.append(
            {
                "symbol": row["symbol"],
                "score": row["score"],
                "trend_bias": row["trend_bias"],
                "in_live_rules": in_live_rules,
                "recommendation": "LIVE_READY" if passes and in_live_rules else "PROMOTE" if passes else "REVIEW",
                "last_close": row["last_close"],
                "position_in_range": row["position_in_range"],
                "momentum_pct": row["momentum_pct"],
            }
        )

    recommended_promotions = [
        row["symbol"]
        for row in shortlist_rows
        if row["recommendation"] == "PROMOTE"
    ]

    rank_left, rank_right = st.columns([1.15, 0.85])
    with rank_left:
        st.markdown('<div class="panel-title">Coin Ranking</div>', unsafe_allow_html=True)
        if ranking["rows"]:
            st.dataframe(ranking["rows"], width='stretch', hide_index=True)

            promotable_symbols = [
                row["symbol"]
                for row in ranking["rows"]
                if row["symbol"] not in config["rules"]
            ]
            with st.form("strategy_promote_ranked_symbols_form"):
                selected_promotions = st.multiselect(
                    "Promote ranked symbols into live rules",
                    promotable_symbols,
                    default=(recommended_promotions or promotable_symbols)[: min(len(recommended_promotions or promotable_symbols), 5)],
                    help="Adds a conservative starter rule for each selected ranked symbol and keeps them in the watchlist.",
                )
                submitted_promotions = st.form_submit_button(
                    "Add Selected Ranked Symbols",
                    width='stretch',
                )
            if submitted_promotions:
                if not selected_promotions:
                    st.warning("Select at least one ranked symbol to promote.")
                else:
                    updated = dict(config)
                    updated_rules = dict(config["rules"])
                    for promoted_symbol in selected_promotions:
                        if promoted_symbol not in updated_rules:
                            updated_rules[promoted_symbol] = build_rule_seed(config, promoted_symbol)
                    updated["rules"] = updated_rules
                    updated["watchlist_symbols"] = sorted(
                        set(config.get("watchlist_symbols", configured_symbols)) | set(selected_promotions)
                    )
                    if _save_config_with_feedback(
                        config,
                        updated,
                        f"Promoted {len(selected_promotions)} ranked symbol(s) into live rules",
                    ):
                        st.rerun()
        else:
            st.caption("No ranked symbols yet. Sync candles first or widen the lookback window.")
    with rank_right:
        st.markdown('<div class="panel-title">Stored Candle Coverage</div>', unsafe_allow_html=True)
        if ranking["coverage"]:
            st.dataframe(ranking["coverage"], width='stretch', hide_index=True)
        else:
            st.caption("No stored candles available yet.")
        if ranking.get("errors"):
            summarized_ranking_errors = _summarize_text_lines(list(ranking["errors"]))
            no_data_count = sum(row["count"] for row in summarized_ranking_errors if "not enough stored candles" in str(row["message"]))
            st.markdown('<div class="panel-title">Ranking Notes</div>', unsafe_allow_html=True)
            st.caption(f"{len(ranking['errors'])} note(s) | insufficient-candle rows {no_data_count}")
            with st.expander("Ranking Note Summary", expanded=False):
                st.dataframe(summarized_ranking_errors, width='stretch', hide_index=True)

    st.markdown('<div class="panel-title">Auto Entry Shortlist</div>', unsafe_allow_html=True)
    st.caption(
        f"Shortlist uses current config filters: min_score >= {auto_entry_min_score:.1f}, biases = {', '.join(sorted(auto_entry_allowed_biases))}."
    )
    shortlist_left, shortlist_right = st.columns([1.0, 1.0])
    with shortlist_left:
        live_ready_rows = [row for row in shortlist_rows if row["recommendation"] == "LIVE_READY"]
        if live_ready_rows:
            st.dataframe(live_ready_rows[:12], width='stretch', hide_index=True)
        else:
            st.caption("No current live rules pass the auto-entry shortlist filters.")
    with shortlist_right:
        promote_rows = [row for row in shortlist_rows if row["recommendation"] == "PROMOTE"]
        if promote_rows:
            st.dataframe(promote_rows[:12], width='stretch', hide_index=True)
        else:
            st.caption("No extra watchlist symbols are currently strong enough to promote.")

    st.markdown('<div class="panel-title">Live Rule Tuning</div>', unsafe_allow_html=True)
    st.caption(
        "This section scores the current live rules against the active auto-entry gate and a candle replay using the same lookback window. "
        "Use it to decide which symbols to keep, review, or prune before widening live automation."
    )

    tuning_rows = build_live_rule_tuning_rows(
        config=config,
        ranking_rows=ranking["rows"],
        ranking_resolution=ranking_resolution,
        ranking_days=ranking_days,
    )
    keep_count = sum(1 for row in tuning_rows if row["recommendation"] == "KEEP")
    review_count = sum(1 for row in tuning_rows if row["recommendation"] == "REVIEW")
    prune_count = sum(1 for row in tuning_rows if row["recommendation"] == "PRUNE")
    tuning_cards = st.columns(4)
    with tuning_cards[0]:
        render_metric_card("Live Rules Reviewed", str(len(tuning_rows)), f"Resolution {ranking_resolution}")
    with tuning_cards[1]:
        render_metric_card("Keep", str(keep_count), f"Monitor {sum(1 for row in tuning_rows if row['recommendation'] == 'MONITOR')}")
    with tuning_cards[2]:
        render_metric_card("Review", str(review_count), f"Prune {prune_count}")
    with tuning_cards[3]:
        total_replay_pnl = sum(float(row.get("replay_pnl_thb", 0.0) or 0.0) for row in tuning_rows)
        render_metric_card("Replay PnL Sum", f"{total_replay_pnl:,.2f} THB", f"Lookback {ranking_days} day(s)")

    tuning_left, tuning_right = st.columns([1.1, 0.9])
    with tuning_left:
        if tuning_rows:
            st.dataframe(tuning_rows, width='stretch', hide_index=True)
        else:
            st.caption("No live rules configured yet. Promote ranked symbols or add rules first.")
    with tuning_right:
        tuning_focus_options = [row["symbol"] for row in tuning_rows]
        if tuning_focus_options:
            focus_symbol = st.selectbox(
                "Rule Focus",
                tuning_focus_options,
                index=0,
                key="strategy_tuning_focus_symbol",
            )
            focus_row = next(row for row in tuning_rows if row["symbol"] == focus_symbol)
            st.markdown(
                badge(
                    f"{focus_row['symbol']} -> {focus_row['recommendation']}",
                    "good" if focus_row["recommendation"] == "KEEP" else "warn" if focus_row["recommendation"] in {"MONITOR", "REVIEW"} else "bad",
                ),
                unsafe_allow_html=True,
            )
            st.caption(f"Auto-entry gate: {focus_row['auto_entry_pass']} | {focus_row['gate_reason']}")
            st.caption(
                f"Replay: trades={focus_row['replay_trades']} pnl={focus_row['replay_pnl_thb']:,.2f} THB win_rate={focus_row['replay_win_rate']:.2f}%"
            )
            st.caption(
                f"Rule: buy_below={focus_row['buy_below']:,.8f} sell_above={focus_row['sell_above']:,.8f} stop={focus_row['stop_loss_percent']:.2f}% take={focus_row['take_profit_percent']:.2f}%"
            )
            st.caption(f"Tuning note: {focus_row['tuning_note']}")
        else:
            st.caption("No tuning focus available yet.")

    st.markdown('<div class="panel-title">Strategy Compare Lab</div>', unsafe_allow_html=True)
    st.caption(
        "Compare a few parameter variants for one live-rule symbol at once, then apply the winning rule back into config if it looks better than the current setup."
    )

    compare_default_symbol = st.session_state.get("strategy_compare_symbol", configured_symbols[0] if configured_symbols else symbols[0])
    if compare_default_symbol not in symbols:
        compare_default_symbol = symbols[0]
    compare_source_options = ["candles", "snapshots"]
    compare_default_source = str(st.session_state.get("strategy_compare_source", "candles"))
    if compare_default_source not in compare_source_options:
        compare_default_source = "candles"
    compare_default_resolution = str(st.session_state.get("strategy_compare_resolution", ranking_resolution))
    if compare_default_resolution not in rank_resolution_options:
        compare_default_resolution = ranking_resolution

    with st.form("strategy_compare_form"):
        compare_meta_left, compare_meta_right = st.columns(2)
        with compare_meta_left:
            compare_symbol = st.selectbox(
                "Compare Symbol",
                configured_symbols or symbols,
                index=(configured_symbols or symbols).index(compare_default_symbol),
            )
            compare_source = st.selectbox(
                "Compare Source",
                compare_source_options,
                index=compare_source_options.index(compare_default_source),
            )
        with compare_meta_right:
            compare_resolution = st.selectbox(
                "Compare Resolution",
                rank_resolution_options,
                index=rank_resolution_options.index(compare_default_resolution),
                help="Used when Compare Source = candles.",
            )
            compare_days = st.number_input(
                "Compare Lookback Days",
                min_value=1,
                max_value=90,
                value=int(st.session_state.get("strategy_compare_days", ranking_days)),
                step=1,
            )
        run_compare = st.form_submit_button("Run Compare", type="primary", width='stretch')

    if configured_symbols:
        if run_compare or "strategy_compare_payload" not in st.session_state:
            compare_base_rule = build_rule_seed(config, compare_symbol)
            compare_variants = build_rule_compare_variants(base_rule=compare_base_rule)
            compare_rows = run_strategy_compare_rows(
                symbol=compare_symbol,
                replay_source=compare_source,
                replay_resolution=str(compare_resolution),
                lookback_days=int(compare_days),
                fee_rate=float(config["fee_rate"]),
                cooldown_seconds=int(config["cooldown_seconds"]),
                variants=compare_variants,
            )
            compare_rows = annotate_strategy_compare_rows(compare_rows)
            st.session_state["strategy_compare_payload"] = {
                "symbol": compare_symbol,
                "source": compare_source,
                "resolution": str(compare_resolution),
                "days": int(compare_days),
                "rows": compare_rows,
                "variant_rules": {row["variant"]: dict(row["rule"]) for row in compare_rows},
            }
            st.session_state["strategy_compare_symbol"] = compare_symbol
            st.session_state["strategy_compare_source"] = compare_source
            st.session_state["strategy_compare_resolution"] = str(compare_resolution)
            st.session_state["strategy_compare_days"] = int(compare_days)

        compare_payload = st.session_state.get("strategy_compare_payload")
        if compare_payload:
            compare_rows = list(compare_payload.get("rows") or [])
            profitable_variants = sum(1 for row in compare_rows if float(row.get("total_pnl_thb", 0.0) or 0.0) > 0)
            best_variant = next((row for row in compare_rows if str(row.get("decision")) in {"Clearly better", "Marginally better"}), compare_rows[0] if compare_rows else None)
            compare_cards = st.columns(4)
            with compare_cards[0]:
                render_metric_card("Compared Variants", str(len(compare_rows)), f"Symbol {compare_payload.get('symbol', 'n/a')}")
            with compare_cards[1]:
                render_metric_card("Profitable Variants", str(profitable_variants), f"Source {compare_payload.get('source', 'n/a')}")
            with compare_cards[2]:
                render_metric_card("Best Candidate", str(best_variant.get('variant', 'n/a')) if best_variant else "n/a", str(best_variant.get('decision', 'n/a')) if best_variant else "n/a")
            with compare_cards[3]:
                render_metric_card("Best Win Rate", f"{float(best_variant.get('win_rate_percent', 0.0) if best_variant else 0.0):.2f}%", f"Lookback {compare_payload.get('days', 'n/a')} day(s)")

            compare_left, compare_right = st.columns([1.1, 0.9])
            with compare_left:
                st.dataframe(
                    [
                        {key: value for key, value in row.items() if key not in {"rule", "decision_rank"}}
                        for row in compare_rows
                    ],
                    width='stretch',
                    hide_index=True,
                )
            with compare_right:
                focus_variant_options = [row["variant"] for row in compare_rows]
                selected_variant = st.selectbox(
                    "Variant Focus",
                    focus_variant_options,
                    index=0,
                    key="strategy_compare_focus_variant",
                )
                focus_variant_row = next(row for row in compare_rows if row["variant"] == selected_variant)
                decision_tone = "good" if str(focus_variant_row.get("decision")) in {"Clearly better", "Marginally better"} else "warn" if str(focus_variant_row.get("decision")) in {"Tied with baseline", "Needs more samples", "Current baseline"} else "bad"
                st.markdown(
                    badge(
                        f"{focus_variant_row['variant']} | {focus_variant_row['decision']}",
                        decision_tone,
                    ),
                    unsafe_allow_html=True,
                )
                st.caption(
                    f"Rule: buy_below={focus_variant_row['buy_below']:,.8f} sell_above={focus_variant_row['sell_above']:,.8f} stop={focus_variant_row['stop_loss_percent']:.2f}% take={focus_variant_row['take_profit_percent']:.2f}%"
                )
                st.caption(
                    f"Replay: trades={focus_variant_row['trades']} win_rate={focus_variant_row['win_rate_percent']:.2f}% hold={focus_variant_row['avg_hold_minutes']:.1f} min open_position={focus_variant_row['open_position']}"
                )
                st.caption(f"Decision: {focus_variant_row['decision_reason']}")
                st.caption(f"Variant note: {focus_variant_row['note']}")

                with st.form("strategy_apply_compared_variant_form"):
                    apply_variant = st.selectbox(
                        "Apply Variant To Live Rule",
                        focus_variant_options,
                        index=focus_variant_options.index(selected_variant),
                    )
                    apply_submitted = st.form_submit_button(
                        "Apply Compared Variant",
                        width='stretch',
                    )
                if apply_submitted:
                    chosen_rule = dict(compare_payload.get("variant_rules", {}).get(apply_variant) or {})
                    if not chosen_rule:
                        st.warning("Could not find the selected variant rule to apply. Run Compare again.")
                    else:
                        updated = dict(config)
                        updated_rules = dict(config["rules"])
                        updated_rules[str(compare_payload["symbol"])] = chosen_rule
                        updated["rules"] = updated_rules
                        if _save_config_with_feedback(
                            config,
                            updated,
                            f"Applied compared variant {apply_variant} to {compare_payload['symbol']}",
                        ):
                            st.rerun()
        else:
            st.caption("Run Compare to evaluate multiple variants for one live-rule symbol.")
    else:
        st.caption("No live rules configured yet, so compare mode is unavailable.")

    st.markdown('<div class="panel-title">Replay Lab</div>', unsafe_allow_html=True)
    st.caption(
        "Replay can now run on stored candles or stored market snapshots. "
        "Use candles for ranked symbols first; snapshots remain available for the older console-style feed."
    )

    default_symbol = str(st.session_state.get("strategy_replay_symbol", symbols[0]))
    if default_symbol not in symbols:
        default_symbol = symbols[0]
    replay_source_options = ["candles", "snapshots"]
    default_replay_source = str(st.session_state.get("strategy_replay_source", "candles"))
    if default_replay_source not in replay_source_options:
        default_replay_source = "candles"
    default_replay_resolution = str(st.session_state.get("strategy_replay_resolution", ranking_resolution))
    if default_replay_resolution not in rank_resolution_options:
        default_replay_resolution = ranking_resolution
    default_replay_days = int(st.session_state.get("strategy_replay_days", min(14, max(14, coverage_days))))

    replay_meta_left, replay_meta_right = st.columns(2)
    with replay_meta_left:
        replay_symbol = st.selectbox(
            "Replay Symbol",
            symbols,
            index=symbols.index(default_symbol),
            key="strategy_replay_symbol",
        )
        replay_source = st.selectbox(
            "Replay Source",
            replay_source_options,
            index=replay_source_options.index(default_replay_source),
            key="strategy_replay_source",
            help="Candles use TradingView history stored in SQLite. Snapshots use the older market_snapshots feed.",
        )
    with replay_meta_right:
        replay_resolution = st.selectbox(
            "Replay Candle Resolution",
            rank_resolution_options,
            index=rank_resolution_options.index(default_replay_resolution),
            key="strategy_replay_resolution",
            help="Used only when Replay Source = candles.",
        )
        lookback_days = st.number_input(
            "Replay Lookback Days",
            min_value=1,
            max_value=90,
            value=default_replay_days,
            step=1,
            key="strategy_replay_days",
        )

    active_rule = build_rule_seed(config, replay_symbol)
    replay_snapshot_cards = st.columns(4)
    with replay_snapshot_cards[0]:
        render_metric_card("Rule Buy Below", f"{float(active_rule['buy_below']):,.8f}", replay_symbol)
    with replay_snapshot_cards[1]:
        render_metric_card("Rule Sell Above", f"{float(active_rule['sell_above']):,.8f}", f"Budget {float(active_rule['budget_thb']):,.2f} THB")
    with replay_snapshot_cards[2]:
        render_metric_card("Stop Loss", f"{float(active_rule['stop_loss_percent']):.2f}%", f"Take profit {float(active_rule['take_profit_percent']):.2f}%")
    with replay_snapshot_cards[3]:
        render_metric_card("Max Trades / Day", str(int(active_rule['max_trades_per_day'])), f"Cooldown {int(config['cooldown_seconds'])} sec")

    with st.form("strategy_replay_form"):
        form_left, form_right = st.columns(2)
        with form_left:
            buy_below = st.number_input("Buy Below", min_value=0.0, value=float(active_rule["buy_below"]), format="%.8f", key=f"strategy_replay_buy_below_{replay_symbol}")
            sell_above = st.number_input("Sell Above", min_value=0.0, value=float(active_rule["sell_above"]), format="%.8f", key=f"strategy_replay_sell_above_{replay_symbol}")
            budget_thb = st.number_input("Budget THB", min_value=1.0, value=float(active_rule["budget_thb"]), step=10.0, key=f"strategy_replay_budget_{replay_symbol}")
            max_trades_per_day = st.number_input(
                "Max Trades / Day",
                min_value=1,
                value=int(active_rule["max_trades_per_day"]),
                step=1,
                key=f"strategy_replay_max_trades_{replay_symbol}",
            )
        with form_right:
            stop_loss_percent = st.number_input(
                "Stop Loss %",
                min_value=0.01,
                value=float(active_rule["stop_loss_percent"]),
                format="%.2f",
                key=f"strategy_replay_stop_loss_{replay_symbol}",
            )
            take_profit_percent = st.number_input(
                "Take Profit %",
                min_value=0.01,
                value=float(active_rule["take_profit_percent"]),
                format="%.2f",
                key=f"strategy_replay_take_profit_{replay_symbol}",
            )
            cooldown_seconds = st.number_input(
                "Cooldown Seconds",
                min_value=0,
                value=int(config["cooldown_seconds"]),
                step=1,
                key=f"strategy_replay_cooldown_{replay_symbol}",
            )
            fee_rate = st.number_input(
                "Fee Rate",
                min_value=0.0,
                max_value=0.9999,
                value=float(config["fee_rate"]),
                format="%.6f",
                key=f"strategy_replay_fee_{replay_symbol}",
            )
        run_replay = st.form_submit_button("Run Replay", type="primary", width='stretch')

    replay_request = {
        "symbol": replay_symbol,
        "source": replay_source,
        "resolution": str(replay_resolution),
        "days": int(lookback_days),
        "buy_below": float(buy_below),
        "sell_above": float(sell_above),
        "budget_thb": float(budget_thb),
        "stop_loss_percent": float(stop_loss_percent),
        "take_profit_percent": float(take_profit_percent),
        "max_trades_per_day": int(max_trades_per_day),
        "cooldown_seconds": int(cooldown_seconds),
        "fee_rate": float(fee_rate),
    }
    prior_replay_request = st.session_state.get("strategy_replay_request")
    if run_replay or "strategy_replay_result" not in st.session_state or prior_replay_request != replay_request:
        replay_rule = {
            "buy_below": float(buy_below),
            "sell_above": float(sell_above),
            "budget_thb": float(budget_thb),
            "stop_loss_percent": float(stop_loss_percent),
            "take_profit_percent": float(take_profit_percent),
            "max_trades_per_day": int(max_trades_per_day),
        }
        if replay_source == "candles":
            replay_result = run_market_candle_replay(
                symbol=replay_symbol,
                resolution=str(replay_resolution),
                rule=replay_rule,
                fee_rate=float(fee_rate),
                cooldown_seconds=int(cooldown_seconds),
                days=int(lookback_days),
            )
        else:
            replay_result = run_market_snapshot_replay(
                symbol=replay_symbol,
                rule=replay_rule,
                fee_rate=float(fee_rate),
                cooldown_seconds=int(cooldown_seconds),
                days=int(lookback_days),
            )
        st.session_state["strategy_replay_result"] = replay_result
        st.session_state["strategy_replay_request"] = replay_request

    replay = st.session_state.get("strategy_replay_result")
    if replay:
        metrics = replay["metrics"]
        replay_card1, replay_card2, replay_card3, replay_card4 = st.columns(4)
        with replay_card1:
            render_metric_card("Replay Trades", str(metrics["trades"]), f"{replay.get('source', 'replay')} rows {replay.get('candles', replay.get('snapshots', replay.get('bars', 0)))}")
        with replay_card2:
            render_metric_card("Replay PnL", f"{metrics['total_pnl_thb']:,.2f} THB", f"Win rate {metrics['win_rate_percent']:.2f}%")
        with replay_card3:
            render_metric_card("Replay Avg/Trade", f"{metrics['avg_pnl_thb']:,.2f}", f"Profit factor {metrics['profit_factor']:.2f}")
        with replay_card4:
            render_metric_card("Replay Hold", f"{metrics['avg_hold_minutes']:.1f} min", f"W {metrics['wins']} / L {metrics['losses']}")

        replay_left, replay_right = st.columns([1.05, 0.95])
        with replay_left:
            st.markdown('<div class="panel-title">Replay Trades</div>', unsafe_allow_html=True)
            if replay["trades"]:
                st.dataframe(replay["trades"], width='stretch', hide_index=True)
            else:
                st.caption("No replay exits were generated for this symbol and parameter set.")
        with replay_right:
            st.markdown('<div class="panel-title">Replay Coverage</div>', unsafe_allow_html=True)
            coverage = replay.get("coverage")
            if coverage:
                st.caption(f"first_seen={coverage['first_seen']}")
                st.caption(f"last_seen={coverage['last_seen']}")
                st.caption(f"min_price={float(coverage['min_price']):,.8f}")
                st.caption(f"max_price={float(coverage['max_price']):,.8f}")
            else:
                st.caption("No snapshot coverage available.")

            if replay.get("open_position"):
                st.markdown('<div class="panel-title">Open Position at Replay End</div>', unsafe_allow_html=True)
                st.json(replay["open_position"], expanded=False)

            if replay.get("notes"):
                st.markdown('<div class="panel-title">Replay Notes</div>', unsafe_allow_html=True)
                for note in replay["notes"]:
                    st.caption(note)

    st.markdown('<div class="panel-title">Snapshot Coverage by Symbol</div>', unsafe_allow_html=True)
    if coverage_rows:
        st.dataframe(coverage_rows, width='stretch', hide_index=True)
    else:
        st.caption("No market snapshot coverage was found in SQLite yet.")


def render_reports_page(*, today: str, config: dict[str, Any]) -> None:
    symbols = ["ALL"] + sorted(config["rules"].keys())
    selected_symbol = st.selectbox("Report Filter", symbols, index=0)
    report = fetch_reporting_summary(today=today, symbol=None if selected_symbol == "ALL" else selected_symbol)
    symbol_summary = report["symbol_summary"]
    recent_execution_orders = report["recent_execution_orders"]
    recent_auto_exit_events = report["recent_auto_exit_events"]
    recent_errors = report["recent_errors"]
    recent_trades = report["recent_trades"]

    total_signals = sum(int(row.get("signals", 0)) for row in symbol_summary)
    total_trades = sum(int(row.get("trades", 0)) for row in symbol_summary)
    total_pnl = sum(float(row.get("pnl_thb", 0.0)) for row in symbol_summary)
    total_wins = sum(int(row.get("wins", 0)) for row in symbol_summary)
    total_losses = sum(int(row.get("losses", 0)) for row in symbol_summary)

    card1, card2, card3, card4 = st.columns(4)
    with card1:
        render_metric_card("Report Filter", selected_symbol, f"Symbols {len(symbol_summary)}")
    with card2:
        render_metric_card("Signals Today", str(total_signals), f"Trades {total_trades}")
    with card3:
        render_metric_card("PnL Today", f"{total_pnl:,.2f} THB", f"W {total_wins} / L {total_losses}")
    with card4:
        render_metric_card(
            "Execution Activity",
            str(len(recent_execution_orders)),
            f"Auto exits {len(recent_auto_exit_events)} | Errors {len(recent_errors)}",
        )

    left, right = st.columns([1.15, 0.85])
    with left:
        st.markdown('<div class="panel-title">Symbol Summary</div>', unsafe_allow_html=True)
        if symbol_summary:
            st.dataframe(symbol_summary, width='stretch', hide_index=True)
        else:
            st.caption("No symbol summary rows for the current filter and date.")
    with right:
        st.markdown('<div class="panel-title">Recent Paper Trades</div>', unsafe_allow_html=True)
        if recent_trades:
            st.dataframe(recent_trades, width='stretch', hide_index=True)
        else:
            st.caption("No paper trades stored for this filter yet.")

    bottom_left, bottom_right = st.columns([1.0, 1.0])
    with bottom_left:
        st.markdown('<div class="panel-title">Recent Execution Orders</div>', unsafe_allow_html=True)
        if recent_execution_orders:
            st.dataframe(recent_execution_orders, width='stretch', hide_index=True)
        else:
            st.caption("No execution orders stored for this filter yet.")
        st.markdown('<div class="panel-title">Recent Auto Exit Events</div>', unsafe_allow_html=True)
        if recent_auto_exit_events:
            st.dataframe(recent_auto_exit_events, width='stretch', hide_index=True)
        else:
            st.caption("No auto-exit events stored for this filter yet.")
    with bottom_right:
        st.markdown('<div class="panel-title">Recent Runtime Errors</div>', unsafe_allow_html=True)
        if recent_errors:
            st.dataframe(recent_errors, width='stretch', hide_index=True)
        else:
            st.caption("No recent runtime errors recorded.")


