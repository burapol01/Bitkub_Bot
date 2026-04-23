"""Strategy Proposal Inbox: bot-proposes, human-approves workflow.

One page, one button. The flow is:

  1. User clicks "Refresh & recompute".
  2. Live tuning scores every live rule (cheap replay pass).
  3. PRUNE-flagged symbols skip the Compare step and become prune proposals.
  4. Remaining symbols run Compare and become rule-update proposals.
  5. Each proposal is tiered (AUTO_APPROVE / RECOMMENDED / NEEDS_REVIEW /
     BLOCKED) so the operator only scrutinises the exceptions.
  6. Bulk actions apply updates or remove rules (optionally also cleaning the
     watchlist).

Kept intentionally separate from the legacy Strategy page so the two can coexist
during rollout.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import streamlit as st

from config import ordered_unique_symbols
from services import strategy_proposal_ledger as ledger
from services import strategy_proposal_service as sps
from services.strategy_proposal_service import (
    ProposalKind,
    ProposalTier,
    PruneProposal,
    RuleProposal,
    build_prune_proposal,
    build_rule_update_proposal,
    group_proposals_by_tier,
    summarize_proposal_counts,
)
from ui.streamlit.config_support import save_config_with_feedback
from ui.streamlit.strategy_support import (
    build_live_rule_tuning_rows,
    build_rule_compare_variants,
    run_strategy_compare_rows,
)
from ui.streamlit.styles import (
    badge,
    render_callout,
    render_metric_card,
    render_section_intro,
)
from ui.streamlit.symbol_state import (
    build_symbol_operational_state,
    build_symbol_operational_state_context,
)
from services.strategy_lab_service import build_coin_ranking
from utils.time_utils import now_text


INBOX_STATE_KEY = "strategy_inbox_snapshot"
INBOX_REMOVE_WATCHLIST_KEY = "strategy_inbox_remove_watchlist"

DEFAULT_RESOLUTION = "240"
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_FEE_RATE = 0.0025
DEFAULT_COOLDOWN_SECONDS = 120

_TIER_TONE: dict[str, str] = {
    ProposalTier.AUTO_APPROVE.value: "good",
    ProposalTier.RECOMMENDED.value: "warn",
    ProposalTier.NEEDS_REVIEW.value: "warn",
    ProposalTier.BLOCKED.value: "bad",
}

_TIER_LABEL: dict[str, str] = {
    ProposalTier.AUTO_APPROVE.value: "Auto-approve ready",
    ProposalTier.RECOMMENDED.value: "Recommended",
    ProposalTier.NEEDS_REVIEW.value: "Needs review",
    ProposalTier.BLOCKED.value: "Blocked",
}

_UPDATE_TIER_ORDER: tuple[str, ...] = (
    ProposalTier.AUTO_APPROVE.value,
    ProposalTier.RECOMMENDED.value,
    ProposalTier.NEEDS_REVIEW.value,
    ProposalTier.BLOCKED.value,
)

_PRUNE_TIER_ORDER: tuple[str, ...] = _UPDATE_TIER_ORDER


def persist_recompute_to_ledger(
    snapshot: dict[str, Any],
    *,
    resolution: str,
    lookback_days: int,
    now: datetime | None = None,
) -> ledger.UpsertResult:
    """Write recompute results to the ledger as pending proposals."""

    proposals: list[Any] = []
    proposals.extend(snapshot.get("updates") or [])
    proposals.extend(snapshot.get("prunes") or [])
    return ledger.upsert_pending(
        proposals,
        resolution=str(resolution),
        lookback_days=int(lookback_days),
        now=now,
    )


def _rule_proposal_from_row(row: ledger.LedgerRow) -> RuleProposal:
    data = dict(row.payload)
    data["tier"] = ProposalTier(data.get("tier") or ProposalTier.NEEDS_REVIEW.value)
    data["proposal_id"] = row.proposal_id
    return RuleProposal(**data)


def _prune_proposal_from_row(row: ledger.LedgerRow) -> PruneProposal:
    data = dict(row.payload)
    data["tier"] = ProposalTier(data.get("tier") or ProposalTier.BLOCKED.value)
    data["proposal_id"] = row.proposal_id
    return PruneProposal(**data)


def load_active_inbox(
    *, now: datetime | None = None
) -> dict[str, list[Any]]:
    """Read active proposals from the ledger as reconstructed dataclasses."""

    updates = [
        _rule_proposal_from_row(row)
        for row in ledger.list_active(kind=ProposalKind.RULE_UPDATE.value, now=now)
    ]
    prunes = [
        _prune_proposal_from_row(row)
        for row in ledger.list_active(kind=ProposalKind.PRUNE.value, now=now)
    ]
    return {"updates": updates, "prunes": prunes}


def _tuning_row_is_prune(tuning_row: dict[str, Any]) -> bool:
    recommendation = str(tuning_row.get("recommendation") or "").upper()
    confidence = str(tuning_row.get("confidence") or "").upper()
    return recommendation == "PRUNE" or confidence == "HIGH_PRUNE"


def recompute_proposals(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    latest_prices: dict[str, float] | None = None,
    resolution: str = DEFAULT_RESOLUTION,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    fee_rate: float = DEFAULT_FEE_RATE,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ranking_builder=build_coin_ranking,
    tuning_builder=build_live_rule_tuning_rows,
    compare_runner=run_strategy_compare_rows,
    op_context_builder=build_symbol_operational_state_context,
    op_state_builder=build_symbol_operational_state,
) -> dict[str, Any]:
    """Build rule-update + prune proposals for every live rule.

    Dependencies are injected so callers (tests) can stub out the heavy
    ranking / replay / account snapshots without touching Streamlit caches.
    """

    private_ctx = dict(private_ctx or {})
    runtime = dict(runtime or {})
    latest_prices = {str(k): float(v) for k, v in dict(latest_prices or {}).items()}

    rules = dict(config.get("rules") or {})
    ranking_symbols = sorted({str(symbol) for symbol in rules}) or sorted(
        str(symbol) for symbol in (config.get("watchlist_symbols") or [])
    )

    ranking_payload: dict[str, Any] = {"rows": []}
    if ranking_symbols:
        try:
            ranking_payload = ranking_builder(
                symbols=list(ranking_symbols),
                resolution=str(resolution),
                lookback_days=int(lookback_days),
            )
        except Exception as exc:  # noqa: BLE001 — surface as skipped, not crash
            ranking_payload = {"rows": [], "error": str(exc)}

    ranking_rows = list(ranking_payload.get("rows") or [])

    tuning_rows = tuning_builder(
        config=config,
        ranking_rows=ranking_rows,
        ranking_resolution=str(resolution),
        ranking_days=int(lookback_days),
    )
    tuning_by_symbol = {str(row.get("symbol") or ""): row for row in tuning_rows}

    op_context = op_context_builder(
        account_snapshot=private_ctx.get("account_snapshot"),
        latest_prices=latest_prices,
    )

    updates: list[RuleProposal] = []
    prunes: list[PruneProposal] = []
    skipped: list[dict[str, str]] = []

    for symbol in sorted(rules):
        current_rule = dict(rules[symbol])
        tuning_row = dict(tuning_by_symbol.get(symbol) or {})

        op_state = op_state_builder(
            symbol=symbol,
            config=config,
            account_snapshot=private_ctx.get("account_snapshot"),
            latest_prices=latest_prices,
            runtime=runtime,
            precomputed_context=op_context,
        )

        baseline_pnl = float(tuning_row.get("replay_pnl_thb", 0.0) or 0.0)
        fee_guardrail = str(tuning_row.get("fee_guardrail") or "")

        if _tuning_row_is_prune(tuning_row):
            prunes.append(
                build_prune_proposal(
                    symbol=symbol,
                    operational_state=op_state,
                    tuning_row=tuning_row,
                    baseline_pnl_thb=baseline_pnl,
                    best_pnl_thb=baseline_pnl,
                    fee_guardrail=fee_guardrail,
                )
            )
            continue

        try:
            variants = build_rule_compare_variants(base_rule=current_rule)
            compare_rows = compare_runner(
                symbol=str(symbol),
                replay_source="candles",
                replay_resolution=str(resolution),
                lookback_days=int(lookback_days),
                fee_rate=float(fee_rate),
                cooldown_seconds=int(cooldown_seconds),
                variants=variants,
            )
        except Exception as exc:  # noqa: BLE001
            skipped.append({"symbol": symbol, "reason": f"compare error: {exc}"})
            continue

        freshness_status = _freshness_from_compare_rows(compare_rows)
        proposal = build_rule_update_proposal(
            symbol=str(symbol),
            current_rule=current_rule,
            compare_rows=compare_rows,
            freshness_status=freshness_status,
        )
        if proposal is None:
            skipped.append({"symbol": symbol, "reason": "no compare rows produced"})
            continue
        updates.append(proposal)

    return {
        "updates": updates,
        "prunes": prunes,
        "skipped": skipped,
        "snapshot_ts": now_text(),
        "resolution": str(resolution),
        "lookback_days": int(lookback_days),
        "ranking_errors": list(ranking_payload.get("errors") or []),
    }


def _freshness_from_compare_rows(compare_rows: list[dict[str, Any]]) -> str:
    if not compare_rows:
        return "Missing"
    bars = max(int(row.get("bars", 0) or 0) for row in compare_rows)
    if bars <= 0:
        return "Missing"
    if bars < 20:
        return "Stale"
    return "Fresh"


def render_strategy_inbox_page(
    *,
    config: dict[str, Any],
    private_ctx: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    latest_prices: dict[str, float] | None = None,
    quote_fetched_at: str | None = None,
) -> None:
    private_ctx = dict(private_ctx or {})
    runtime = dict(runtime or {})
    latest_prices = dict(latest_prices or {})

    render_section_intro(
        "Strategy Proposal Inbox",
        "Bot calculates, you approve. Profitable coins get rule-update proposals, "
        "unprofitable ones go straight to the prune queue — no wasted recompute.",
        "Proposal Inbox",
    )

    session_meta = dict(st.session_state.get(INBOX_STATE_KEY) or {})

    header_cols = st.columns([3, 2, 2])
    with header_cols[0]:
        if st.button(
            "Refresh prices & recompute proposals",
            key="strategy_inbox_recompute",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Recomputing proposals…"):
                snapshot = recompute_proposals(
                    config=config,
                    private_ctx=private_ctx,
                    runtime=runtime,
                    latest_prices=latest_prices,
                )
                persist_recompute_to_ledger(
                    snapshot,
                    resolution=str(snapshot.get("resolution")),
                    lookback_days=int(snapshot.get("lookback_days") or 0),
                )
            st.session_state[INBOX_STATE_KEY] = {
                "snapshot_ts": snapshot.get("snapshot_ts"),
                "resolution": snapshot.get("resolution"),
                "lookback_days": snapshot.get("lookback_days"),
                "skipped": snapshot.get("skipped") or [],
                "ranking_errors": snapshot.get("ranking_errors") or [],
            }
            st.rerun()
    with header_cols[1]:
        st.caption(
            f"Latest quotes: {quote_fetched_at or 'unknown'}  ·  "
            f"{len(latest_prices)} symbols"
        )
    with header_cols[2]:
        if session_meta:
            st.caption(
                f"Last recompute: {session_meta.get('snapshot_ts') or 'n/a'}  ·  "
                f"lookback {session_meta.get('lookback_days')}d @ {session_meta.get('resolution')}"
            )
        else:
            st.caption("Showing persisted proposals — click recompute for fresh signal.")

    active = load_active_inbox()
    updates: list[RuleProposal] = list(active.get("updates") or [])
    prunes: list[PruneProposal] = list(active.get("prunes") or [])
    skipped = list(session_meta.get("skipped") or [])
    ranking_errors = list(session_meta.get("ranking_errors") or [])

    if not updates and not prunes and not session_meta:
        render_callout(
            "Getting started",
            "Run <em>Refresh prices & recompute proposals</em> to score every live rule. "
            "The bot will pre-check confident updates so you can approve them in bulk.",
            "info",
        )
        return

    _render_summary_banner(updates=updates, prunes=prunes, skipped=len(skipped))

    _render_rule_updates_section(config=config, updates=updates)
    st.divider()
    _render_prune_section(config=config, prunes=prunes)

    if skipped:
        with st.expander(f"Skipped ({len(skipped)})", expanded=False):
            for entry in skipped:
                st.caption(f"• {entry['symbol']}: {entry['reason']}")
    if ranking_errors:
        with st.expander(f"Ranking notices ({len(ranking_errors)})", expanded=False):
            for note in ranking_errors:
                st.caption(f"• {note}")


def _render_summary_banner(
    *, updates: list[RuleProposal], prunes: list[PruneProposal], skipped: int
) -> None:
    update_counts = summarize_proposal_counts(updates)
    prune_counts = summarize_proposal_counts(prunes)

    cols = st.columns(5)
    with cols[0]:
        render_metric_card(
            "Auto-approve",
            str(update_counts[ProposalTier.AUTO_APPROVE.value]),
            "Rule updates pre-checked",
        )
    with cols[1]:
        render_metric_card(
            "Recommended",
            str(update_counts[ProposalTier.RECOMMENDED.value]),
            "Updates to review",
        )
    with cols[2]:
        render_metric_card(
            "Needs review",
            str(update_counts[ProposalTier.NEEDS_REVIEW.value]),
            "Manual decision",
        )
    with cols[3]:
        render_metric_card(
            "Prune queue",
            str(len(prunes)),
            (
                f"{prune_counts[ProposalTier.AUTO_APPROVE.value]} ready, "
                f"{prune_counts[ProposalTier.BLOCKED.value]} blocked"
            ),
        )
    with cols[4]:
        render_metric_card(
            "Skipped",
            str(skipped),
            "See diagnostics below",
        )


def _render_rule_updates_section(
    *, config: dict[str, Any], updates: list[RuleProposal]
) -> None:
    st.markdown("### Rule update proposals")
    if not updates:
        st.caption("No rule-update proposals — every live rule is either PRUNE or skipped.")
        return

    buckets = group_proposals_by_tier(updates)

    for tier_value in _UPDATE_TIER_ORDER:
        tier_updates: list[RuleProposal] = list(buckets.get(tier_value) or [])
        if not tier_updates:
            continue
        default_expanded = tier_value in {
            ProposalTier.AUTO_APPROVE.value,
            ProposalTier.RECOMMENDED.value,
        }
        with st.expander(
            f"{_TIER_LABEL[tier_value]}  ·  {len(tier_updates)}",
            expanded=default_expanded,
        ):
            _render_rule_update_bucket(
                config=config,
                tier=tier_value,
                proposals=tier_updates,
            )


def _render_rule_update_bucket(
    *, config: dict[str, Any], tier: str, proposals: list[RuleProposal]
) -> None:
    form_key = f"strategy_inbox_update_form::{tier}"
    select_key = f"strategy_inbox_update_select::{tier}"
    apply_key = f"strategy_inbox_update_apply::{tier}"
    dismiss_key = f"strategy_inbox_update_dismiss::{tier}"

    options = [p.symbol for p in proposals]
    default = [
        p.symbol
        for p in proposals
        if tier in {ProposalTier.AUTO_APPROVE.value, ProposalTier.RECOMMENDED.value}
        and not p.hard_blocks
    ]

    with st.form(form_key):
        for proposal in proposals:
            _render_rule_proposal_row(proposal)
        selection = st.multiselect(
            "Symbols to act on",
            options=options,
            default=default,
            key=select_key,
        )
        action_cols = st.columns(2)
        with action_cols[0]:
            apply_clicked = st.form_submit_button(
                f"Apply {len(selection)} update(s) to live config",
                key=apply_key,
                type="primary" if tier == ProposalTier.AUTO_APPROVE.value else "secondary",
                disabled=tier == ProposalTier.BLOCKED.value,
            )
        with action_cols[1]:
            dismiss_clicked = st.form_submit_button(
                f"Dismiss {len(selection)} selected",
                key=dismiss_key,
            )

    if apply_clicked and selection and tier != ProposalTier.BLOCKED.value:
        _apply_rule_updates(config=config, proposals=proposals, selected_symbols=selection)
    elif dismiss_clicked and selection:
        _dismiss_proposals(proposals=proposals, selected_symbols=selection)


def _render_rule_proposal_row(proposal: RuleProposal) -> None:
    header = (
        f"<strong>{proposal.symbol}</strong>  "
        f"{badge(proposal.best_variant or 'n/a', 'info')}  "
        f"{badge(f'conf {proposal.confidence:.2f}', _TIER_TONE.get(proposal.tier.value, 'info'))}  "
        f"{badge(proposal.freshness_status, 'good' if proposal.freshness_status == 'Fresh' else 'warn')}"
    )
    st.markdown(header, unsafe_allow_html=True)

    cols = st.columns(3)
    with cols[0]:
        render_metric_card(
            "Baseline PnL",
            f"{proposal.baseline_pnl_thb:+.2f} THB",
            f"{proposal.trades} replay trade(s)",
        )
    with cols[1]:
        render_metric_card(
            "Proposed PnL",
            f"{proposal.proposed_pnl_thb:+.2f} THB",
            f"edge {proposal.edge_thb:+.2f} THB",
        )
    with cols[2]:
        render_metric_card(
            "Win rate",
            f"{proposal.win_rate_percent:.1f}%",
            f"fee guardrail {proposal.fee_guardrail or 'n/a'}",
        )

    diff = _rule_diff_summary(proposal.current_rule, proposal.proposed_rule)
    if diff:
        st.caption("Rule change: " + diff)
    if proposal.reason:
        st.caption(f"Reason: {proposal.reason}")
    for warning in proposal.warnings:
        st.warning(warning)
    for block in proposal.hard_blocks:
        st.error(f"Blocked: {block}")
    st.markdown("---")


def _rule_diff_summary(current: dict[str, Any], proposed: dict[str, Any]) -> str:
    parts: list[str] = []
    keys = (
        "buy_below",
        "sell_above",
        "stop_loss_percent",
        "take_profit_percent",
        "max_trades_per_day",
        "budget_thb",
    )
    for key in keys:
        if key not in current or key not in proposed:
            continue
        before = current.get(key)
        after = proposed.get(key)
        if before is None or after is None:
            continue
        try:
            before_f = float(before)
            after_f = float(after)
        except (TypeError, ValueError):
            continue
        if abs(before_f - after_f) <= 1e-9:
            continue
        parts.append(f"{key} {before_f:g} → {after_f:g}")
    return ", ".join(parts)


def _render_prune_section(
    *, config: dict[str, Any], prunes: list[PruneProposal]
) -> None:
    st.markdown("### Prune queue")
    if not prunes:
        st.caption("No PRUNE-flagged live rules right now.")
        return

    buckets = group_proposals_by_tier(prunes)

    remove_watchlist = st.checkbox(
        "Also remove selected symbols from watchlist",
        key=INBOX_REMOVE_WATCHLIST_KEY,
        value=False,
        help="Leave unchecked to keep the symbol in the research watchlist.",
    )

    for tier_value in _PRUNE_TIER_ORDER:
        tier_prunes: list[PruneProposal] = list(buckets.get(tier_value) or [])
        if not tier_prunes:
            continue
        default_expanded = tier_value in {
            ProposalTier.AUTO_APPROVE.value,
            ProposalTier.RECOMMENDED.value,
            ProposalTier.BLOCKED.value,
        }
        with st.expander(
            f"{_TIER_LABEL[tier_value]}  ·  {len(tier_prunes)}",
            expanded=default_expanded,
        ):
            _render_prune_bucket(
                config=config,
                tier=tier_value,
                proposals=tier_prunes,
                remove_watchlist=remove_watchlist,
            )


def _render_prune_bucket(
    *,
    config: dict[str, Any],
    tier: str,
    proposals: list[PruneProposal],
    remove_watchlist: bool,
) -> None:
    form_key = f"strategy_inbox_prune_form::{tier}"
    select_key = f"strategy_inbox_prune_select::{tier}"
    apply_key = f"strategy_inbox_prune_apply::{tier}"

    options = [p.symbol for p in proposals]
    default = [
        p.symbol
        for p in proposals
        if tier == ProposalTier.AUTO_APPROVE.value and not p.hard_blocks
    ]

    blocked_tier = tier == ProposalTier.BLOCKED.value

    dismiss_key = f"strategy_inbox_prune_dismiss::{tier}"

    with st.form(form_key):
        for proposal in proposals:
            _render_prune_proposal_row(proposal)

        selection = st.multiselect(
            "Symbols to act on",
            options=options,
            default=default,
            key=select_key,
        )
        action_label = (
            "Remove rule + watchlist" if remove_watchlist else "Remove rule only"
        )
        action_cols = st.columns(2)
        with action_cols[0]:
            apply_clicked = st.form_submit_button(
                f"{action_label} ({len(selection)})",
                key=apply_key,
                type="primary" if tier == ProposalTier.AUTO_APPROVE.value else "secondary",
                disabled=blocked_tier,
            )
        with action_cols[1]:
            dismiss_clicked = st.form_submit_button(
                f"Dismiss {len(selection)} selected",
                key=dismiss_key,
            )

    if apply_clicked and selection and not blocked_tier:
        _apply_prune(
            config=config,
            proposals=proposals,
            selected_symbols=selection,
            remove_watchlist=remove_watchlist,
        )
    elif dismiss_clicked and selection:
        _dismiss_proposals(proposals=proposals, selected_symbols=selection)


def _render_prune_proposal_row(proposal: PruneProposal) -> None:
    tone = _TIER_TONE.get(proposal.tier.value, "info")
    header = (
        f"<strong>{proposal.symbol}</strong>  "
        f"{badge(f'conf {proposal.confidence:.2f}', tone)}  "
        f"{badge(f'buy {proposal.open_buy_count}', 'warn' if proposal.open_buy_count else 'good')}  "
        f"{badge(f'sell {proposal.open_sell_count}', 'warn' if proposal.open_sell_count else 'good')}  "
        f"{badge('ghost reserved', 'warn') if proposal.has_ghost_reserved else ''}"
    )
    st.markdown(header, unsafe_allow_html=True)

    cols = st.columns(3)
    with cols[0]:
        render_metric_card(
            "Reserved THB",
            f"{proposal.reserved_thb:,.2f}",
            f"reserved coin {proposal.reserved_coin:,.8f}",
        )
    with cols[1]:
        render_metric_card(
            "Baseline PnL",
            f"{proposal.baseline_pnl_thb:+.2f} THB",
            f"tuning: {proposal.tuning_recommendation or 'n/a'}",
        )
    with cols[2]:
        render_metric_card(
            "Best PnL",
            f"{proposal.best_pnl_thb:+.2f} THB",
            "skipped compare — prune-flagged",
        )

    if proposal.reason:
        st.caption(f"Reason: {proposal.reason}")
    for warning in proposal.warnings:
        st.warning(warning)
    for block in proposal.hard_blocks:
        st.error(f"Blocked: {block}")
    st.markdown("---")


def apply_rule_update_action(
    *,
    config: dict[str, Any],
    proposals: list[RuleProposal],
    selected_symbols: list[str],
    save_config_fn=save_config_with_feedback,
    mark_applied_fn=ledger.mark_applied,
    actor_id: str = "inbox_user",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply rule updates to config and mark ledger entries applied.

    Returns a summary ``{"applied": [(symbol, proposal_id)], "skipped": [symbol]}``
    so callers (UI, tests) can report outcomes without touching Streamlit.
    """

    proposal_by_symbol = {p.symbol: p for p in proposals}
    updated = dict(config)
    rules = dict(updated.get("rules") or {})

    applied: list[tuple[str, str]] = []
    skipped: list[str] = []
    for symbol in selected_symbols:
        proposal = proposal_by_symbol.get(symbol)
        if proposal is None:
            continue
        if proposal.hard_blocks:
            skipped.append(symbol)
            continue
        rules[symbol] = dict(proposal.proposed_rule)
        applied.append((symbol, proposal.proposal_id))

    if not applied:
        return {"applied": [], "skipped": skipped}

    updated["rules"] = rules
    applied_symbols = [symbol for symbol, _ in applied]
    save_config_fn(
        config,
        updated,
        f"Applied rule updates: {', '.join(applied_symbols)}",
        audit_action_type="strategy_inbox_rule_update",
        audit_metadata={"applied_symbols": applied_symbols},
    )
    for symbol, proposal_id in applied:
        if not proposal_id:
            continue
        mark_applied_fn(
            proposal_id,
            actor_id=actor_id,
            metadata={"symbol": symbol, "source": "strategy_inbox"},
            now=now,
        )
    return {"applied": applied, "skipped": skipped}


def apply_prune_action(
    *,
    config: dict[str, Any],
    proposals: list[PruneProposal],
    selected_symbols: list[str],
    remove_watchlist: bool,
    save_config_fn=save_config_with_feedback,
    mark_applied_fn=ledger.mark_applied,
    actor_id: str = "inbox_user",
    now: datetime | None = None,
) -> dict[str, Any]:
    prune_set = {str(symbol) for symbol in selected_symbols if str(symbol).strip()}
    if not prune_set:
        return {"applied": [], "skipped": []}

    proposal_by_symbol = {p.symbol: p for p in proposals}

    applied: list[tuple[str, str]] = []
    skipped: list[str] = []
    for symbol in sorted(prune_set):
        proposal = proposal_by_symbol.get(symbol)
        if proposal is None:
            skipped.append(symbol)
            continue
        applied.append((symbol, proposal.proposal_id))

    updated = dict(config)
    current_rules = dict(updated.get("rules") or {})
    remaining_rules = {
        symbol: rule for symbol, rule in current_rules.items() if symbol not in prune_set
    }
    updated["rules"] = remaining_rules

    current_watchlist = [
        str(symbol)
        for symbol in (
            updated.get("watchlist_symbols") or sorted(current_rules.keys())
        )
        if isinstance(symbol, str) and str(symbol).strip()
    ]
    if remove_watchlist:
        updated["watchlist_symbols"] = [
            symbol for symbol in current_watchlist if symbol not in prune_set
        ]
    else:
        updated["watchlist_symbols"] = ordered_unique_symbols(
            current_watchlist,
            remaining_rules.keys(),
        )

    action = "Remove rule + watchlist" if remove_watchlist else "Remove rule"
    save_config_fn(
        config,
        updated,
        f"{action}: {', '.join(sorted(prune_set))}",
        audit_action_type="strategy_inbox_prune",
        audit_metadata={
            "pruned_symbols": sorted(prune_set),
            "remove_from_watchlist": bool(remove_watchlist),
        },
    )
    for symbol, proposal_id in applied:
        if not proposal_id:
            continue
        mark_applied_fn(
            proposal_id,
            actor_id=actor_id,
            metadata={"symbol": symbol, "remove_from_watchlist": bool(remove_watchlist)},
            now=now,
        )
    return {"applied": applied, "skipped": skipped}


def dismiss_proposals_action(
    *,
    proposals: list[Any],
    selected_symbols: list[str],
    mark_dismissed_fn=ledger.mark_dismissed,
    actor_id: str = "inbox_user",
    reason: str = "dismissed via Strategy Inbox",
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Mark selected proposals dismissed in the ledger."""

    proposal_by_symbol = {p.symbol: p for p in proposals}
    dismissed: list[tuple[str, str]] = []
    for symbol in selected_symbols:
        proposal = proposal_by_symbol.get(symbol)
        if proposal is None or not getattr(proposal, "proposal_id", ""):
            continue
        mark_dismissed_fn(
            proposal.proposal_id,
            actor_id=actor_id,
            reason=reason,
            now=now,
        )
        dismissed.append((symbol, proposal.proposal_id))
    return dismissed


def _apply_rule_updates(
    *,
    config: dict[str, Any],
    proposals: list[RuleProposal],
    selected_symbols: list[str],
) -> None:
    if not selected_symbols:
        return
    outcome = apply_rule_update_action(
        config=config,
        proposals=proposals,
        selected_symbols=selected_symbols,
    )
    if not outcome["applied"]:
        st.warning("No updates applied — selected proposals were blocked.")
        return
    st.rerun()


def _apply_prune(
    *,
    config: dict[str, Any],
    proposals: list[PruneProposal],
    selected_symbols: list[str],
    remove_watchlist: bool,
) -> None:
    if not selected_symbols:
        return
    outcome = apply_prune_action(
        config=config,
        proposals=proposals,
        selected_symbols=selected_symbols,
        remove_watchlist=remove_watchlist,
    )
    if not outcome["applied"]:
        st.warning("No prunes applied — selected proposals could not be resolved.")
        return
    st.rerun()


def _dismiss_proposals(
    *,
    proposals: list[Any],
    selected_symbols: list[str],
) -> None:
    if not selected_symbols:
        return
    dismissed = dismiss_proposals_action(
        proposals=proposals,
        selected_symbols=selected_symbols,
    )
    if dismissed:
        st.toast(f"Dismissed {len(dismissed)} proposal(s)")
    st.rerun()


__all__ = [
    "INBOX_STATE_KEY",
    "apply_prune_action",
    "apply_rule_update_action",
    "dismiss_proposals_action",
    "load_active_inbox",
    "persist_recompute_to_ledger",
    "recompute_proposals",
    "render_strategy_inbox_page",
]
