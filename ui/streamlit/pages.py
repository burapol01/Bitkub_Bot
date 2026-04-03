from __future__ import annotations

from collections import defaultdict
from typing import Any

import streamlit as st

from config import CONFIG_PATH

from services.db_service import (
    DB_PATH,
    fetch_reports_page_dataset,
    fetch_runtime_event_log,
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
from ui.streamlit.config_support import render_config_page, save_config_with_feedback
from ui.streamlit.ops_pages import render_account_page, render_live_ops_page, render_overview_page
from ui.streamlit.diagnostics_support import render_diagnostics_page, render_logs_page
from ui.streamlit.data import calc_daily_totals, capability_badge_tone
from ui.streamlit.refresh import PAGE_ORDER, render_refreshable_fragment
from ui.streamlit.styles import badge, render_callout, render_metric_card, render_section_intro, render_sidebar_block
from ui.streamlit.strategy_support import (
    annotate_strategy_compare_rows,
    build_live_rule_tuning_rows,
    build_rule_compare_variants,
    build_rule_seed,
    evaluate_fee_guardrail,
    fetch_market_symbol_universe,
    run_strategy_compare_rows,
)
from utils.time_utils import now_text


def _summarize_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, int] = defaultdict(int)
    for line in lines:
        grouped[str(line)] += 1
    return [
        {"message": message, "count": count}
        for message, count in sorted(grouped.items(), key=lambda item: (-item[1], item[0]))
    ]


def _sync_multiselect_state(*, key: str, options: list[str], default: list[str]) -> None:
    normalized_options = [str(option) for option in options]
    normalized_default = [
        str(value) for value in default if str(value) in normalized_options
    ]
    signature = (tuple(normalized_options), tuple(normalized_default))
    signature_key = f"{key}__signature"
    current_value = st.session_state.get(key)

    if st.session_state.get(signature_key) != signature:
        st.session_state[key] = list(normalized_default)
        st.session_state[signature_key] = signature
        return

    if isinstance(current_value, list):
        filtered_value = [
            str(value) for value in current_value if str(value) in normalized_options
        ]
        if filtered_value != list(current_value):
            st.session_state[key] = filtered_value


@st.cache_data(ttl=30, show_spinner=False)
def _cached_trade_analytics(symbol_key: str = "") -> dict[str, Any]:
    return fetch_trade_analytics(symbol=symbol_key or None)


@st.cache_data(ttl=30, show_spinner=False)
def _cached_market_snapshot_coverage(days: int) -> list[dict[str, Any]]:
    return fetch_market_snapshot_coverage(days=days)


@st.cache_data(ttl=20, show_spinner=False)
def _cached_reports_page_payload(
    *,
    today: str,
    days: int,
    symbol_key: str = "",
) -> dict[str, Any]:
    return fetch_reports_page_dataset(
        today=today,
        days=days,
        symbol=symbol_key or None,
    )


@st.cache_data(ttl=60, show_spinner=False)
def _cached_coin_ranking(
    *,
    symbols: tuple[str, ...],
    resolution: str,
    lookback_days: int,
) -> dict[str, Any]:
    return build_coin_ranking(
        symbols=list(symbols),
        resolution=resolution,
        lookback_days=lookback_days,
    )


@st.cache_data(ttl=20, show_spinner=False)
def _cached_strategy_tuning_history(limit: int = 8) -> list[dict[str, Any]]:
    return fetch_runtime_event_log(limit=limit, event_type="strategy_tuning")


@st.cache_data(ttl=20, show_spinner=False)
def _cached_auto_entry_review_events(limit: int = 40) -> list[dict[str, Any]]:
    return fetch_runtime_event_log(limit=limit, event_type="auto_live_entry_review")


def _build_auto_entry_review_report(limit: int = 40) -> dict[str, Any]:
    events = _cached_auto_entry_review_events(limit=limit)
    rejection_counts: dict[str, int] = defaultdict(int)
    symbol_reject_counts: dict[str, int] = defaultdict(int)
    symbol_candidate_counts: dict[str, int] = defaultdict(int)
    top_candidate_rows: list[dict[str, Any]] = []
    latest_context: dict[str, Any] = {}

    for index, row in enumerate(events):
        details = dict(row.get("details") or {})
        candidates = list(details.get("candidates") or [])
        rejected = list(details.get("rejected") or [])
        if index == 0:
            latest_context = dict(details.get("ranking_context") or {})
        for candidate in candidates:
            symbol = str(candidate.get("symbol") or "")
            if symbol:
                symbol_candidate_counts[symbol] += 1
            top_candidate_rows.append(
                {
                    "created_at": row.get("created_at"),
                    "symbol": symbol or "n/a",
                    "ranking_score": float(candidate.get("ranking_score") or 0.0),
                    "entry_discount_percent": float(candidate.get("entry_discount_percent") or 0.0),
                    "trend_bias": str(candidate.get("trend_bias") or "n/a"),
                }
            )
        for rejected_row in rejected:
            symbol = str(rejected_row.get("symbol") or "")
            if symbol:
                symbol_reject_counts[symbol] += 1
            for reason in list(rejected_row.get("reasons") or []):
                rejection_counts[str(reason)] += 1

    top_candidate_rows.sort(
        key=lambda item: (
            -float(item.get("ranking_score") or 0.0),
            -float(item.get("entry_discount_percent") or 0.0),
            str(item.get("symbol") or ""),
        )
    )

    return {
        "events": events,
        "latest_context": latest_context,
        "rejection_summary": [
            {"reason": reason, "count": count}
            for reason, count in sorted(rejection_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "symbol_reject_summary": [
            {"symbol": symbol, "rejections": count}
            for symbol, count in sorted(symbol_reject_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "symbol_candidate_summary": [
            {"symbol": symbol, "candidate_hits": count}
            for symbol, count in sorted(symbol_candidate_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "top_candidates": top_candidate_rows[:20],
    }


def _build_pruned_live_rules_config(
    *,
    config: dict[str, Any],
    symbols_to_prune: list[str],
    remove_from_watchlist: bool,
) -> dict[str, Any]:
    prune_set = {str(symbol) for symbol in symbols_to_prune if str(symbol).strip()}
    updated = dict(config)
    updated_rules = {
        symbol: rule
        for symbol, rule in dict(config.get("rules", {})).items()
        if symbol not in prune_set
    }
    updated["rules"] = updated_rules

    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", sorted(config.get("rules", {}).keys()))
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    if remove_from_watchlist:
        updated["watchlist_symbols"] = [
            symbol for symbol in watchlist_symbols if symbol not in prune_set
        ]
    else:
        updated["watchlist_symbols"] = sorted(set(watchlist_symbols) | set(updated_rules))
    return updated


def render_sidebar(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any],
    selected_page: str,
    version_label: str,
    version_detail: str,
) -> str:
    with st.sidebar:
        render_sidebar_block(
            "Workspace",
            f"Mode <strong>{str(config['mode']).upper()}</strong><br>Config <code>{CONFIG_PATH}</code><br>SQLite <code>{DB_PATH}</code>",
        )
        render_sidebar_block(
            "Deployment",
            f"Version <strong>{version_label}</strong><br>{version_detail}",
        )
        st.markdown("### Navigation")
        if st.session_state.get("sidebar_page") not in PAGE_ORDER:
            st.session_state["sidebar_page"] = selected_page
        page_name = st.radio(
            "Page",
            PAGE_ORDER,
            key="sidebar_page",
            label_visibility="collapsed",
        )
        render_sidebar_block(
            "Current Page",
            f"<strong>{page_name}</strong><br>Use Refresh Dashboard after config changes or when you want a full rerender.",
        )
        if st.button("Refresh Dashboard", width='stretch'):
            st.rerun()

        st.markdown("### Status")
        status_badges = [badge(f"Mode {str(config['mode']).upper()}", "info")]
        st.markdown(" ".join(status_badges), unsafe_allow_html=True)
        render_sidebar_block(
            "Private API",
            str(private_ctx["private_api_status"]),
        )
        for item in private_ctx["private_api_capabilities"]:
            tone = capability_badge_tone(item)
            st.markdown(badge(item, tone), unsafe_allow_html=True)

        render_sidebar_block(
            "Guideline",
            "Overview = health first<br>Live Ops = real actions<br>Strategy = tune rules before widening automation<br>Logs = debug only when summary says something is wrong",
        )
    return str(page_name)


def render_strategy_page(*, config: dict[str, Any]) -> None:
    render_section_intro(
        "Strategy Lab",
        "Research first, then tighten live rules. Watchlist symbols are your research universe, while config rules remain the live shortlist.",
        "Research & Tuning",
    )
    render_section_intro("Strategy Workflow", "Follow this order to avoid overreacting to single metrics or isolated replays.", "Workflow")
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

    strategy_workspace_options = [
        "Overview",
        "Sync & Rank",
        "Live Tuning",
        "Compare",
        "Replay",
    ]
    default_strategy_workspace = str(st.session_state.get("strategy_workspace", "Sync & Rank"))
    if default_strategy_workspace not in strategy_workspace_options:
        default_strategy_workspace = "Sync & Rank"
    strategy_workspace = st.radio(
        "Strategy Workspace",
        strategy_workspace_options,
        index=strategy_workspace_options.index(default_strategy_workspace),
        horizontal=True,
        key="strategy_workspace",
    )
    render_callout(
        "Workspace Focus",
        {
            "Overview": "Lightweight summary of actual paper-trade analytics only.",
            "Sync & Rank": "Sync candles, inspect ranking, and decide which symbols deserve live attention.",
            "Live Tuning": "Run the expensive live-rule review, fee guardrails, and auto-entry review report only when you are actively tuning.",
            "Compare": "Compare variants for one live symbol without reloading ranking and tuning matrices.",
            "Replay": "Manual deep-dive for a single symbol with its own replay controls and coverage.",
        }[strategy_workspace],
        "info",
    )

    should_show_overview = strategy_workspace == "Overview"
    should_show_ranking = strategy_workspace == "Sync & Rank"
    should_show_tuning = strategy_workspace == "Live Tuning"
    should_show_compare = strategy_workspace == "Compare"
    should_show_replay = strategy_workspace == "Replay"

    configured_symbols = sorted(config["rules"].keys())
    watchlist_symbols = [
        str(symbol)
        for symbol in config.get("watchlist_symbols", configured_symbols)
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    coverage_days = int(config.get("market_snapshot_retention_days", 30))
    rank_resolution_options = ["1", "5", "15", "60", "240", "1D"]
    default_rank_resolution = str(st.session_state.get("strategy_rank_resolution", "240"))
    if default_rank_resolution not in rank_resolution_options:
        default_rank_resolution = "240"
    default_rank_days = int(st.session_state.get("strategy_rank_days", 14))
    ranking_resolution = str(st.session_state.get("strategy_rank_resolution", default_rank_resolution))
    ranking_days = int(st.session_state.get("strategy_rank_days", default_rank_days))

    auto_entry_min_score = float(config.get("live_auto_entry_min_score", 50.0))
    auto_entry_allowed_biases = {
        str(value).strip().lower()
        for value in config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"])
        if str(value).strip()
    } or {"bullish", "mixed"}

    market_universe = fetch_market_symbol_universe() if (should_show_ranking or should_show_replay) else {"symbols": [], "error": None}
    market_symbols = list(market_universe.get("symbols", []))
    symbols = market_symbols or watchlist_symbols or configured_symbols or ["THB_BTC"]

    ranking: dict[str, Any] = {"rows": [], "coverage": [], "errors": []}
    ranking_last_close_by_symbol: dict[str, float] = {}
    coverage_rows = _cached_market_snapshot_coverage(days=coverage_days) if should_show_replay else []

    if should_show_overview:
        analytics = _cached_trade_analytics()
        totals = analytics["totals"]

        card1, card2, card3, card4, card5 = st.columns(5)
        with card1:
            render_metric_card("Actual Trades", str(totals["trades"]), f"Win rate {totals['win_rate_percent']:.2f}%")
        with card2:
            render_metric_card("Actual PnL", f"{totals['total_pnl_thb']:,.2f} THB", f"Avg/trade {totals['avg_pnl_thb']:,.2f}")
        with card3:
            render_metric_card("Profit Factor", f"{totals['profit_factor']:.2f}", f"Avg win {totals['avg_win_thb']:,.2f}")
        with card4:
            render_metric_card("Hold Time", f"{totals['avg_hold_minutes']:.1f} min", f"Losses {totals['losses']}")
        with card5:
            render_metric_card("Actual Fees", f"{totals.get('total_fee_thb', 0.0):,.2f} THB", f"Fee drag {totals.get('fee_drag_percent', 0.0):.2f}%")

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
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

    if should_show_ranking or should_show_tuning:
        ranking_symbol_pool = list(symbols if should_show_ranking else (configured_symbols or watchlist_symbols or symbols))
        ranking = _cached_coin_ranking(
            symbols=tuple(ranking_symbol_pool),
            resolution=ranking_resolution,
            lookback_days=ranking_days,
        )
        ranking_last_close_by_symbol = {
            str(row["symbol"]): float(row.get("last_close", 0.0) or 0.0)
            for row in ranking["rows"]
        }

    if should_show_ranking:
        st.markdown('<div class="panel-title">Candle Sync & Coin Ranking</div>', unsafe_allow_html=True)
        st.caption(
            "Sync TradingView history into SQLite first, then rank coins by recent momentum, range position, stability, and average volume."
        )

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
            st.rerun()

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

        rank_card1, rank_card2, rank_card3 = st.columns(3)
        with rank_card1:
            render_metric_card("Ranking Resolution", ranking_resolution, f"Lookback {ranking_days} day(s)")
        with rank_card2:
            render_metric_card("Ranked Symbols", str(len(ranking["rows"])), f"Coverage rows {len(ranking['coverage'])}")
        with rank_card3:
            top_score = ranking["rows"][0]["score"] if ranking["rows"] else 0.0
            render_metric_card("Top Score", f"{top_score:.2f}", "Higher = stronger trend shortlist")

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

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
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
                if promotable_symbols:
                    promotion_state_key = "strategy_promote_ranked_symbols"
                    default_promotions = (recommended_promotions or promotable_symbols)[
                        : min(len(recommended_promotions or promotable_symbols), 5)
                    ]
                    _sync_multiselect_state(
                        key=promotion_state_key,
                        options=promotable_symbols,
                        default=default_promotions,
                    )
                    with st.form("strategy_promote_ranked_symbols_form"):
                        selected_promotions = st.multiselect(
                            "Promote ranked symbols into live rules",
                            promotable_symbols,
                            key=promotion_state_key,
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
                                    updated_rules[promoted_symbol] = build_rule_seed(
                                        config,
                                        promoted_symbol,
                                        market_price=ranking_last_close_by_symbol.get(promoted_symbol),
                                    )
                            updated["rules"] = updated_rules
                            updated["watchlist_symbols"] = sorted(
                                set(config.get("watchlist_symbols", configured_symbols))
                                | set(selected_promotions)
                            )
                            if save_config_with_feedback(
                                config,
                                updated,
                                f"Promoted {len(selected_promotions)} ranked symbol(s) into live rules",
                            ):
                                st.session_state.pop(promotion_state_key, None)
                                st.session_state.pop(f"{promotion_state_key}__signature", None)
                                st.rerun()
                else:
                    st.info("All ranked symbols in the current shortlist already exist in live rules.")
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

    if should_show_tuning:
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
        keep_rows = [row for row in tuning_rows if row["recommendation"] == "KEEP"]
        monitor_rows = [row for row in tuning_rows if row["recommendation"] == "MONITOR"]
        review_rows = [row for row in tuning_rows if row["recommendation"] == "REVIEW"]
        prune_rows = [row for row in tuning_rows if row["recommendation"] == "PRUNE"]
        strong_keep_count = sum(1 for row in keep_rows if row.get("confidence") == "STRONG_KEEP")
        high_prune_count = sum(1 for row in prune_rows if row.get("confidence") == "HIGH_PRUNE")
        fee_watch_count = sum(1 for row in tuning_rows if str(row.get("fee_guardrail")) in {"FEE_HEAVY", "THIN_EDGE", "LOSS_AFTER_FEES"})
        thin_edge_count = sum(1 for row in tuning_rows if str(row.get("fee_guardrail")) in {"THIN_EDGE", "LOSS_AFTER_FEES"})
        keep_count = len(keep_rows)
        monitor_count = len(monitor_rows)
        review_count = len(review_rows)
        prune_count = len(prune_rows)
        actionable_rows = prune_rows + review_rows + monitor_rows

        tuning_cards = st.columns(4)
        with tuning_cards[0]:
            render_metric_card("Live Rules Reviewed", str(len(tuning_rows)), f"Resolution {ranking_resolution}")
        with tuning_cards[1]:
            render_metric_card("Keep", str(keep_count), f"Strong keep {strong_keep_count} | Monitor {monitor_count}")
        with tuning_cards[2]:
            render_metric_card("Review", str(review_count), f"High prune {high_prune_count} | Prune {prune_count}")
        with tuning_cards[3]:
            total_replay_pnl = sum(float(row.get("replay_pnl_thb", 0.0) or 0.0) for row in tuning_rows)
            render_metric_card("Replay PnL Sum", f"{total_replay_pnl:,.2f} THB", f"Fee watch {fee_watch_count} | Thin edge {thin_edge_count}")

        if prune_rows:
            render_callout(
                "Live Rule Tuning",
                f"{prune_count} live rule(s) are currently marked PRUNE. Default move is to remove them from live rules and keep them in watchlist for research unless you intentionally opt out.",
                "bad",
            )
        elif review_rows:
            render_callout(
                "Live Rule Tuning",
                f"No hard prune candidates right now, but {review_count} symbol(s) should go through Compare Lab before you widen live auto-entry.",
                "warn",
            )
        else:
            render_callout(
                "Live Rule Tuning",
                "Live rules look stable right now. Focus on compare tests and auto-entry monitoring for incremental tuning.",
                "good",
            )

        tuning_left, tuning_right = st.columns([1.15, 0.85])
        with tuning_left:
            tuning_tabs = st.tabs(["Action Queue", "Full Matrix"])
            with tuning_tabs[0]:
                if actionable_rows:
                    action_queue_rows = []
                    for row in actionable_rows:
                        next_step = (
                            "Remove from live rules; keep in watchlist if you still want research coverage."
                            if row["recommendation"] == "PRUNE"
                            else "Run Compare Lab and update the rule before trusting wider automation."
                            if row["recommendation"] == "REVIEW"
                            else "Gather more replay trades before promoting or pruning this rule."
                        )
                        action_queue_rows.append(
                            {
                                "symbol": row["symbol"],
                                "recommendation": row["recommendation"],
                                "confidence": row["confidence"],
                                "market_context": row["market_context"],
                                "entry_gap_percent": row["entry_gap_percent"],
                                "target_gap_percent": row["target_gap_percent"],
                                "gate_reason": row["gate_reason"],
                                "replay_trades": row["replay_trades"],
                                "replay_pnl_thb": row["replay_pnl_thb"],
                                "replay_fee_drag_percent": row.get("replay_fee_drag_percent", 0.0),
                                "fee_guardrail": row.get("fee_guardrail", "n/a"),
                                "replay_win_rate": row["replay_win_rate"],
                                "next_step": next_step,
                            }
                        )
                    st.dataframe(action_queue_rows, width='stretch', hide_index=True)
                else:
                    st.caption("No action queue right now. All current live rules are either stable or still accumulating evidence.")

                if prune_rows:
                    prune_default_symbols = [row["symbol"] for row in prune_rows]
                    prune_option_symbols = [row["symbol"] for row in actionable_rows]
                    with st.form("strategy_prune_live_rules_form"):
                        prune_selection = st.multiselect(
                            "Prune From Live Rules",
                            prune_option_symbols,
                            default=prune_default_symbols,
                            help="Default selection includes symbols currently marked PRUNE. You can also remove REVIEW/MONITOR symbols if you intentionally want a tighter shortlist.",
                        )
                        remove_from_watchlist = st.checkbox(
                            "Also remove from watchlist",
                            value=False,
                            help="Leave this off if you still want ranking, replay, and research coverage for the symbol after pruning it from live rules.",
                        )
                        prune_submitted = st.form_submit_button(
                            "Prune Selected Live Rules",
                            type="primary",
                            width='stretch',
                        )
                    if prune_submitted:
                        if not prune_selection:
                            st.warning("Select at least one symbol before pruning live rules.")
                        else:
                            updated = _build_pruned_live_rules_config(
                                config=config,
                                symbols_to_prune=list(prune_selection),
                                remove_from_watchlist=bool(remove_from_watchlist),
                            )
                            if save_config_with_feedback(
                                config,
                                updated,
                                f"Pruned {len(prune_selection)} symbol(s) from live rules",
                            ):
                                insert_runtime_event(
                                    created_at=now_text(),
                                    event_type="strategy_tuning",
                                    severity="info",
                                    message=f"Pruned {len(prune_selection)} symbol(s) from live rules",
                                    details={
                                        "action": "prune_live_rules",
                                        "symbols": list(prune_selection),
                                        "remove_from_watchlist": bool(remove_from_watchlist),
                                    },
                                )
                                _cached_strategy_tuning_history.clear()
                                st.rerun()
            with tuning_tabs[1]:
                if tuning_rows:
                    st.dataframe(tuning_rows, width='stretch', hide_index=True)
                else:
                    st.caption("No live rules configured yet. Promote ranked symbols or add rules first.")

        with tuning_right:
            tuning_focus_options = [row["symbol"] for row in tuning_rows]
            if tuning_focus_options:
                default_focus_symbol = (
                    st.session_state.pop("strategy_tuning_focus_autorun", None)
                    or (
                        prune_rows[0]["symbol"]
                        if prune_rows
                        else review_rows[0]["symbol"]
                        if review_rows
                        else monitor_rows[0]["symbol"]
                        if monitor_rows
                        else tuning_focus_options[0]
                    )
                )
                if default_focus_symbol not in tuning_focus_options:
                    default_focus_symbol = tuning_focus_options[0]
                current_focus_symbol = st.session_state.get("strategy_tuning_focus_symbol", default_focus_symbol)
                if current_focus_symbol not in tuning_focus_options:
                    current_focus_symbol = default_focus_symbol
                focus_index = tuning_focus_options.index(current_focus_symbol)
                focus_symbol = st.selectbox(
                    "Rule Focus",
                    tuning_focus_options,
                    index=focus_index,
                    key="strategy_tuning_focus_symbol",
                )
                focus_row = next(row for row in tuning_rows if row["symbol"] == focus_symbol)
                st.markdown(
                    badge(
                        f"{focus_row['symbol']} -> {focus_row['recommendation']} | {focus_row['confidence']}",
                        "good" if focus_row["recommendation"] == "KEEP" else "warn" if focus_row["recommendation"] in {"MONITOR", "REVIEW"} else "bad",
                    ),
                    unsafe_allow_html=True,
                )
                st.caption(f"Auto-entry gate: {focus_row['auto_entry_pass']} | {focus_row['gate_reason']}")
                st.caption(
                    f"Replay: trades={focus_row['replay_trades']} pnl={focus_row['replay_pnl_thb']:,.2f} THB win_rate={focus_row['replay_win_rate']:.2f}% hold={focus_row['replay_avg_hold_min']:.1f} min"
                )
                st.caption(
                    f"Fees: total={focus_row.get('replay_total_fee_thb', 0.0):,.2f} THB avg={focus_row.get('replay_avg_fee_thb', 0.0):,.2f} fee_drag={focus_row.get('replay_fee_drag_percent', 0.0):.2f}% -> {focus_row.get('fee_guardrail', 'n/a')}"
                )
                st.caption(
                    f"Market: last={focus_row['last_close']:,.8f} | {focus_row['market_context']} | entry gap {focus_row['entry_gap_percent']:+.2f}% | target gap {focus_row['target_gap_percent']:+.2f}%"
                )
                st.caption(
                    f"Rule: buy_below={focus_row['buy_below']:,.8f} sell_above={focus_row['sell_above']:,.8f} stop_ref={focus_row['stop_reference']:,.8f} ({focus_row['stop_gap_percent']:+.2f}%) take={focus_row['take_profit_percent']:.2f}%"
                )
                st.caption(f"Tuning note: {focus_row['tuning_note']}")
                st.caption(f"Confidence: {focus_row['confidence_note']}")
                st.caption(f"Fee guardrail: {focus_row.get('fee_guardrail_note', 'n/a')}")
                focus_next_step = (
                    "Next step: keep this symbol live and monitor Latest Auto Entry Review for execution quality."
                    if focus_row["recommendation"] == "KEEP"
                    else "Next step: run Strategy Compare Lab on this symbol before widening automation."
                    if focus_row["recommendation"] == "REVIEW"
                    else "Next step: gather more samples before changing the rule."
                    if focus_row["recommendation"] == "MONITOR"
                    else "Next step: prune this symbol from live rules unless you have a strong reason to keep it live."
                )
                st.info(focus_next_step)
            else:
                st.caption("No tuning focus available yet.")

    if should_show_compare:
        render_section_intro(
            "Strategy Compare Lab",
            "Compare a few parameter variants for one live-rule symbol at once, then apply the winner back into config only when it beats the current baseline clearly enough.",
            "Compare",
        )

        compare_symbol_options = configured_symbols or symbols
        compare_default_symbol = st.session_state.get("strategy_compare_symbol", compare_symbol_options[0])
        if compare_default_symbol not in compare_symbol_options:
            compare_default_symbol = compare_symbol_options[0]
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
                    compare_symbol_options,
                    index=compare_symbol_options.index(compare_default_symbol),
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
            compare_autorun = st.session_state.pop("strategy_compare_autorun", None)
            should_run_compare = run_compare or "strategy_compare_payload" not in st.session_state or bool(compare_autorun)
            if should_run_compare:
                if compare_autorun:
                    compare_symbol = str(compare_autorun.get("symbol", compare_symbol))
                    compare_source = str(compare_autorun.get("source", compare_source))
                    compare_resolution = str(compare_autorun.get("resolution", compare_resolution))
                    compare_days = int(compare_autorun.get("days", compare_days))
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
                    render_metric_card("Best Fee Drag", f"{float(best_variant.get('fee_drag_percent', 0.0) if best_variant else 0.0):.2f}%", f"Lookback {compare_payload.get('days', 'n/a')} day(s)")

                st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
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
                    compare_scope = "|".join(
                        [
                            str(compare_payload.get("symbol", "")),
                            str(compare_payload.get("source", "")),
                            str(compare_payload.get("resolution", "")),
                            str(compare_payload.get("days", "")),
                        ]
                    )
                    preferred_variant = (
                        str(best_variant.get("variant"))
                        if best_variant and str(best_variant.get("variant")) in focus_variant_options
                        else focus_variant_options[0]
                    )
                    selected_variant = st.selectbox(
                        "Variant Focus",
                        focus_variant_options,
                        index=focus_variant_options.index(preferred_variant),
                        key=f"strategy_compare_focus_variant::{compare_scope}",
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
                    st.caption(
                        f"Fees: total={float(focus_variant_row.get('total_fee_thb', 0.0) or 0.0):,.2f} THB | fee drag={float(focus_variant_row.get('fee_drag_percent', 0.0) or 0.0):.2f}% | {focus_variant_row.get('fee_guardrail', 'n/a')}"
                    )
                    st.caption(f"Fee note: {focus_variant_row.get('fee_guardrail_note', 'n/a')}")
                    st.caption(f"Decision: {focus_variant_row['decision_reason']}")
                    st.caption(f"Variant note: {focus_variant_row['note']}")

                    with st.form("strategy_apply_compared_variant_form"):
                        apply_variant = st.selectbox(
                            "Apply Variant To Live Rule",
                            focus_variant_options,
                            index=focus_variant_options.index(selected_variant),
                            key=f"strategy_compare_apply_variant::{compare_scope}::{selected_variant}",
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
                            if save_config_with_feedback(
                                config,
                                updated,
                                f"Applied compared variant {apply_variant} to {compare_payload['symbol']}",
                            ):
                                insert_runtime_event(
                                    created_at=now_text(),
                                    event_type="strategy_tuning",
                                    severity="info",
                                    message=f"Applied variant {apply_variant} to {compare_payload['symbol']}",
                                    details={
                                        "action": "apply_compared_variant",
                                        "symbol": str(compare_payload["symbol"]),
                                        "variant": str(apply_variant),
                                        "source": str(compare_payload.get("source", compare_source)),
                                        "resolution": str(compare_payload.get("resolution", compare_resolution)),
                                        "days": int(compare_payload.get("days", compare_days)),
                                        "rule": chosen_rule,
                                    },
                                )
                                _cached_strategy_tuning_history.clear()
                                st.session_state["strategy_compare_symbol"] = str(compare_payload["symbol"])
                                st.session_state["strategy_tuning_focus_autorun"] = str(compare_payload["symbol"])
                                st.session_state.pop("strategy_compare_payload", None)
                                st.session_state["strategy_compare_autorun"] = {
                                    "symbol": str(compare_payload["symbol"]),
                                    "source": str(compare_payload.get("source", compare_source)),
                                    "resolution": str(compare_payload.get("resolution", compare_resolution)),
                                    "days": int(compare_payload.get("days", compare_days)),
                                }
                                st.rerun()
            else:
                st.caption("Run Compare to evaluate multiple variants for one live-rule symbol.")
        else:
            st.caption("No live rules configured yet, so compare mode is unavailable.")

    if should_show_tuning:
        auto_entry_report = _build_auto_entry_review_report(limit=40)
        with st.expander("Auto Entry Review Report", expanded=False):
            review_events = list(auto_entry_report.get("events") or [])
            report_context = dict(auto_entry_report.get("latest_context") or {})
            report_cards = st.columns(4)
            with report_cards[0]:
                render_metric_card("Review Events", str(len(review_events)), "Recent auto-entry reviews")
            with report_cards[1]:
                render_metric_card("Top Rejection", str((auto_entry_report.get("rejection_summary") or [{"reason": "n/a"}])[0].get("reason", "n/a")) if auto_entry_report.get("rejection_summary") else "n/a", f"Count {int((auto_entry_report.get('rejection_summary') or [{'count': 0}])[0].get('count', 0))}")
            with report_cards[2]:
                render_metric_card("Most Seen Candidate", str((auto_entry_report.get("symbol_candidate_summary") or [{"symbol": "n/a"}])[0].get("symbol", "n/a")) if auto_entry_report.get("symbol_candidate_summary") else "n/a", f"Hits {int((auto_entry_report.get('symbol_candidate_summary') or [{'candidate_hits': 0}])[0].get('candidate_hits', 0))}")
            with report_cards[3]:
                render_metric_card("Current Gate", f"min_score {float(report_context.get('min_score', 0.0)):.1f}", ", ".join(report_context.get("allowed_biases", [])) or "n/a")

            report_left, report_right = st.columns([1.0, 1.0])
            with report_left:
                st.markdown("#### Rejection Summary")
                if auto_entry_report.get("rejection_summary"):
                    st.dataframe(auto_entry_report["rejection_summary"], width='stretch', hide_index=True)
                else:
                    st.caption("No rejection reasons recorded yet.")
                st.markdown("#### Candidate Frequency")
                if auto_entry_report.get("symbol_candidate_summary"):
                    st.dataframe(auto_entry_report["symbol_candidate_summary"], width='stretch', hide_index=True)
                else:
                    st.caption("No candidates recorded yet.")
            with report_right:
                st.markdown("#### Top Candidate Snapshots")
                if auto_entry_report.get("top_candidates"):
                    st.dataframe(auto_entry_report["top_candidates"], width='stretch', hide_index=True)
                else:
                    st.caption("No candidate snapshots recorded yet.")
                st.markdown("#### Symbols Rejected Most Often")
                if auto_entry_report.get("symbol_reject_summary"):
                    st.dataframe(auto_entry_report["symbol_reject_summary"], width='stretch', hide_index=True)
                else:
                    st.caption("No repeated rejects recorded yet.")

        tuning_history = _cached_strategy_tuning_history(limit=8)
        with st.expander("Recent Tuning Actions", expanded=False):
            if tuning_history:
                st.dataframe(
                    [
                        {
                            "created_at": row["created_at"],
                            "message": row["message"],
                            "action": str((row.get("details") or {}).get("action", "n/a")),
                        }
                        for row in tuning_history
                    ],
                    width='stretch',
                    hide_index=True,
                )
            else:
                st.caption("No tuning actions recorded yet. Apply a variant or prune live rules to build history.")

    if should_show_replay:
        render_section_intro(
            "Replay Lab",
            "Use this for manual deep-dive after ranking and compare. Candles are the default; snapshots remain available for the older console-style feed.",
            "Replay",
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

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
        replay_market_price = ranking_last_close_by_symbol.get(replay_symbol)
        if replay_market_price is None and replay_symbol not in config["rules"] and replay_symbol:
            replay_price_ranking = _cached_coin_ranking(
                symbols=(str(replay_symbol),),
                resolution=str(replay_resolution),
                lookback_days=int(lookback_days),
            )
            if replay_price_ranking.get("rows"):
                replay_market_price = float(replay_price_ranking["rows"][0].get("last_close", 0.0) or 0.0)

        active_rule = build_rule_seed(
            config,
            replay_symbol,
            market_price=replay_market_price,
        )
        replay_snapshot_cards = st.columns(4)
        with replay_snapshot_cards[0]:
            render_metric_card("Rule Buy Below", f"{float(active_rule['buy_below']):,.8f}", replay_symbol)
        with replay_snapshot_cards[1]:
            render_metric_card("Rule Sell Above", f"{float(active_rule['sell_above']):,.8f}", f"Budget {float(active_rule['budget_thb']):,.2f} THB")
        with replay_snapshot_cards[2]:
            render_metric_card("Stop Loss", f"{float(active_rule['stop_loss_percent']):.2f}%", f"Take profit {float(active_rule['take_profit_percent']):.2f}%")
        with replay_snapshot_cards[3]:
            render_metric_card("Max Trades / Day", str(int(active_rule['max_trades_per_day'])), f"Cooldown {int(config['cooldown_seconds'])} sec")

        st.markdown('<div class="page-gap"></div>', unsafe_allow_html=True)
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
                render_metric_card("Replay Avg/Trade", f"{metrics['avg_pnl_thb']:,.2f}", f"Profit factor {metrics['profit_factor']:.2f} | Fee drag {metrics.get('fee_drag_percent', 0.0):.2f}%")
            with replay_card4:
                render_metric_card("Replay Fees", f"{metrics.get('total_fee_thb', 0.0):,.2f} THB", f"Avg fee {metrics.get('avg_fee_thb', 0.0):,.2f} | Hold {metrics['avg_hold_minutes']:.1f} min")

            replay_fee_guardrail, replay_fee_note, _ = evaluate_fee_guardrail(
                trades=int(metrics.get('trades', 0) or 0),
                total_pnl_thb=float(metrics.get('total_pnl_thb', 0.0) or 0.0),
                total_fee_thb=float(metrics.get('total_fee_thb', 0.0) or 0.0),
                avg_pnl_thb=float(metrics.get('avg_pnl_thb', 0.0) or 0.0),
                avg_fee_thb=float(metrics.get('avg_fee_thb', 0.0) or 0.0),
                fee_drag_percent=float(metrics.get('fee_drag_percent', 0.0) or 0.0),
            )
            if replay_fee_guardrail in {"FEE_HEAVY", "THIN_EDGE", "LOSS_AFTER_FEES"}:
                st.warning(f"Replay fee guardrail: {replay_fee_guardrail} | {replay_fee_note}")
            else:
                st.caption(f"Replay fee guardrail: {replay_fee_guardrail} | {replay_fee_note}")

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
    render_section_intro(
        "Reports",
        "Start with daily outcome and fee drag, then use symbol and execution tables for the why behind the numbers.",
        "Reporting",
    )
    symbols = ["ALL"] + sorted(config["rules"].keys())
    report_filter_col, report_window_col = st.columns([0.6, 0.4])
    with report_filter_col:
        selected_symbol = st.selectbox("Report Filter", symbols, index=0)
    with report_window_col:
        daily_window_days = int(
            st.select_slider(
                "Daily Summary Window",
                options=[7, 14, 30, 60],
                value=14,
                key="reports_daily_window",
            )
        )

    selected_symbol_key = "" if selected_symbol == "ALL" else selected_symbol
    reports_payload = _cached_reports_page_payload(
        today=today,
        days=daily_window_days,
        symbol_key=selected_symbol_key,
    )
    report = dict(reports_payload.get("report") or {})
    daily_summary_rows = list(reports_payload.get("daily_summary") or [])
    symbol_summary = report["symbol_summary"]
    recent_execution_orders = report["recent_execution_orders"]
    recent_auto_exit_events = report["recent_auto_exit_events"]
    recent_errors = report["recent_errors"]
    recent_trades = report["recent_trades"]

    total_signals = sum(int(row.get("signals", 0)) for row in symbol_summary)
    total_paper_trades = sum(int(row.get("trades", 0)) for row in symbol_summary)
    total_live_trades = sum(int(row.get("live_closed_trades", 0)) for row in symbol_summary)
    total_combined_trades = total_paper_trades + total_live_trades
    total_paper_pnl = sum(float(row.get("pnl_thb", 0.0)) for row in symbol_summary)
    total_live_pnl = sum(float(row.get("live_realized_pnl_thb", 0.0)) for row in symbol_summary)
    total_combined_pnl = total_paper_pnl + total_live_pnl
    total_paper_fee = sum(float(row.get("paper_fee_thb", 0.0)) for row in symbol_summary)
    total_live_fee = sum(float(row.get("live_fee_thb", 0.0)) for row in symbol_summary)
    total_combined_fee = sum(float(row.get("combined_fee_thb", 0.0)) for row in symbol_summary)

    report_alert_tone = "bad" if total_combined_pnl < 0 else "warn" if total_combined_fee > max(abs(total_combined_pnl) * 0.5, 1.0) else "good"
    report_alert_message = (
        f"PnL {total_combined_pnl:,.2f} THB | Fee {total_combined_fee:,.2f} THB | Signals {total_signals} | Closed trades {total_combined_trades}"
    )
    render_callout("Report Snapshot", report_alert_message, report_alert_tone)

    status_badges = [
        badge(f"Filter {selected_symbol}", "info"),
        badge("PnL NEGATIVE" if total_combined_pnl < 0 else "PnL POSITIVE", "bad" if total_combined_pnl < 0 else "good"),
        badge("Fee HEAVY" if total_combined_fee > max(abs(total_combined_pnl) * 0.5, 1.0) else "Fee OK", "warn" if total_combined_fee > max(abs(total_combined_pnl) * 0.5, 1.0) else "good"),
        badge(f"Errors {len(recent_errors)}", "warn" if recent_errors else "good"),
    ]
    st.markdown(f'<div class="status-strip">{" ".join(status_badges)}</div>', unsafe_allow_html=True)

    card1, card2, card3, card4, card5 = st.columns(5)
    with card1:
        render_metric_card("Report Filter", selected_symbol, f"Symbols {len(symbol_summary)} | Window {daily_window_days}d")
    with card2:
        render_metric_card("Signals Today", str(total_signals), f"Closed {total_combined_trades} trade(s)")
    with card3:
        render_metric_card("Closed Trades", str(total_combined_trades), f"Paper {total_paper_trades} | Live {total_live_trades}")
    with card4:
        render_metric_card(
            "PnL Today",
            f"{total_combined_pnl:,.2f} THB",
            f"Paper {total_paper_pnl:,.2f} | Live {total_live_pnl:,.2f}",
        )
    with card5:
        render_metric_card(
            "Fee Today",
            f"{total_combined_fee:,.2f} THB",
            f"Paper {total_paper_fee:,.2f} | Live {total_live_fee:,.2f} | Errors {len(recent_errors)}",
        )

    render_section_intro(
        "Daily PnL Summary",
        f"Daily realized result for the last {daily_window_days} day(s), including paper and live columns in the same table.",
        "Daily",
    )
    if daily_summary_rows:
        st.dataframe(daily_summary_rows, width='stretch', hide_index=True)
    else:
        st.caption("No daily realized PnL rows were found for the selected filter and window.")

    left, right = st.columns([1.15, 0.85])
    with left:
        render_section_intro("Symbol Summary", "Daily symbol-level rollup. Use this first before jumping into raw execution rows.", "Summary")
        if symbol_summary:
            st.dataframe(symbol_summary, width='stretch', hide_index=True)
        else:
            st.caption("No symbol summary rows for the current filter and date.")
    with right:
        render_section_intro("Recent Paper Trades", "Paper-side closeouts for the selected scope, useful for comparing against live outcomes.", "Paper")
        if recent_trades:
            st.dataframe(recent_trades, width='stretch', hide_index=True)
        else:
            st.caption("No paper trades stored for this filter yet.")

    fee_by_symbol_rows = []
    for row in symbol_summary:
        combined_trades = int(row.get("trades", 0) or 0) + int(row.get("live_closed_trades", 0) or 0)
        combined_pnl = float(row.get("pnl_thb", 0.0) or 0.0) + float(row.get("live_realized_pnl_thb", 0.0) or 0.0)
        combined_fee = float(row.get("combined_fee_thb", 0.0) or 0.0)
        estimated_gross = combined_pnl + combined_fee if combined_pnl > 0 else 0.0
        estimated_fee_drag = (combined_fee * 100.0 / estimated_gross) if estimated_gross > 0 else 0.0
        avg_pnl = (combined_pnl / combined_trades) if combined_trades else 0.0
        avg_fee = (combined_fee / combined_trades) if combined_trades else 0.0
        fee_guardrail, fee_note, _ = evaluate_fee_guardrail(
            trades=combined_trades,
            total_pnl_thb=combined_pnl,
            total_fee_thb=combined_fee,
            avg_pnl_thb=avg_pnl,
            avg_fee_thb=avg_fee,
            fee_drag_percent=estimated_fee_drag,
        )
        fee_by_symbol_rows.append(
            {
                "symbol": row.get("symbol"),
                "paper_fee_thb": row.get("paper_fee_thb", 0.0),
                "live_fee_thb": row.get("live_fee_thb", 0.0),
                "combined_fee_thb": combined_fee,
                "combined_realized_pnl_thb": combined_pnl,
                "closed_trades": combined_trades,
                "fee_drag_percent": estimated_fee_drag,
                "fee_guardrail": fee_guardrail,
                "fee_guardrail_note": fee_note,
            }
        )

    heavy_fee_rows = [row for row in fee_by_symbol_rows if str(row.get("fee_guardrail")) in {"FEE_HEAVY", "THIN_EDGE", "LOSS_AFTER_FEES"}]
    if heavy_fee_rows:
        render_callout(
            "Fee Pressure",
            f"{len(heavy_fee_rows)} symbol(s) are under fee pressure. Review the Fee By Symbol table before widening automation or reducing entry filters.",
            "warn",
        )
    else:
        render_callout(
            "Fee Pressure",
            "No symbol is currently flagged as fee-heavy for the selected report scope.",
            "good",
        )

    fee_left, fee_right = st.columns([1.0, 1.0])
    with fee_left:
        render_section_intro("Fee By Symbol", "Fee drag and realized contribution per symbol so you can see where the edge is being spent.", "Fees")
        if fee_by_symbol_rows:
            st.dataframe(fee_by_symbol_rows, width='stretch', hide_index=True)
        else:
            st.caption("No fee rows for the current filter yet.")
    with fee_right:
        render_section_intro("Fee Notes", "How fee numbers are derived and how to interpret the fee guardrail column.", "Fees")
        st.caption("Paper fees come from paper_trade_logs buy_fee_thb + sell_fee_thb.")
        st.caption("Live fees come from filled execution_orders response_json.result.fee.")
        st.caption("Live realized PnL is estimated from filled buy/sell execution history using FIFO cost basis.")
        st.caption("Fee guardrail flags symbols whose net edge is too thin relative to trading fees.")

    bottom_left, bottom_right = st.columns([1.0, 1.0])
    with bottom_left:
        render_section_intro("Recent Execution Orders", "Latest live execution rows for the selected report scope.", "Execution")
        if recent_execution_orders:
            st.dataframe(recent_execution_orders, width='stretch', hide_index=True)
        else:
            st.caption("No execution orders stored for this filter yet.")
        render_section_intro("Recent Auto Exit Events", "Auto-exit runtime events that help explain why the engine tried to close positions.", "Execution")
        if recent_auto_exit_events:
            st.dataframe(recent_auto_exit_events, width='stretch', hide_index=True)
        else:
            st.caption("No auto-exit events stored for this filter yet.")
    with bottom_right:
        render_section_intro("Recent Runtime Errors", "Read this after the summary if something still looks inconsistent.", "Diagnostics")
        if recent_errors:
            st.dataframe(recent_errors, width='stretch', hide_index=True)
        else:
            st.caption("No recent runtime errors recorded.")


