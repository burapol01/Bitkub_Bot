"""Proposal service for the Strategy workflow redesign.

Produces structured rule-update and prune proposals that the UI can group into
confidence tiers so the operator reviews exceptions instead of every symbol.

Pure Python; no Streamlit or database imports — safe for direct unit testing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable


class ProposalKind(str, Enum):
    RULE_UPDATE = "RULE_UPDATE"
    PRUNE = "PRUNE"


class ProposalTier(str, Enum):
    AUTO_APPROVE = "AUTO_APPROVE"
    RECOMMENDED = "RECOMMENDED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    PRUNE = "PRUNE"
    BLOCKED = "BLOCKED"


DEFAULT_PROPOSAL_TTL_SECONDS = 300
DEFAULT_SNAPSHOT_BUCKET_SECONDS = 300


def rule_hash(rule: dict[str, Any] | None) -> str:
    """Stable sha1 of a rule dict.

    Canonicalised via sorted keys + ensure_ascii so identical logical rules
    hash identically regardless of insertion order.
    """

    payload = json.dumps(dict(rule or {}), sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _parse_iso_to_utc(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        dt = ts
    else:
        dt = datetime.fromisoformat(str(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def stable_proposal_id(
    *,
    symbol: str,
    kind: str,
    rule_hash_value: str,
    snapshot_ts: str | datetime,
    bucket_seconds: int = DEFAULT_SNAPSHOT_BUCKET_SECONDS,
) -> str:
    """Dedup-friendly proposal id.

    Identical (symbol, kind, rule_hash) produced within the same
    ``bucket_seconds`` window collapse to the same id so repeated recomputes
    within a bucket are idempotent on INSERT OR IGNORE.
    """

    dt = _parse_iso_to_utc(snapshot_ts)
    epoch = int(dt.timestamp())
    bucket = epoch // max(1, int(bucket_seconds))
    key = "|".join(
        (
            str(symbol).strip().upper(),
            str(kind).strip().upper(),
            str(rule_hash_value),
            str(bucket),
        )
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()

SOFT_REVIEW_REASONS: frozenset[str] = frozenset(
    {
        "exchange open-orders coverage is partial",
        "exchange open-orders query returned an error",
    }
)


@dataclass
class RuleProposal:
    symbol: str
    tier: ProposalTier
    confidence: float
    current_rule: dict[str, Any]
    proposed_rule: dict[str, Any]
    reason: str
    warnings: list[str] = field(default_factory=list)
    hard_blocks: list[str] = field(default_factory=list)
    best_variant: str = ""
    baseline_pnl_thb: float = 0.0
    proposed_pnl_thb: float = 0.0
    edge_thb: float = 0.0
    win_rate_percent: float = 0.0
    trades: int = 0
    fee_guardrail: str = ""
    freshness_status: str = ""
    snapshot_ts: str = ""
    expires_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tier"] = self.tier.value
        return data

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return reference >= expires


@dataclass
class PruneProposal:
    symbol: str
    tier: ProposalTier
    confidence: float
    reason: str
    warnings: list[str] = field(default_factory=list)
    hard_blocks: list[str] = field(default_factory=list)
    remove_from_watchlist: bool = False
    open_buy_count: int = 0
    open_sell_count: int = 0
    reserved_thb: float = 0.0
    reserved_coin: float = 0.0
    partial_fill: bool = False
    has_real_open_orders: bool = False
    has_ghost_reserved: bool = False
    tuning_recommendation: str = ""
    baseline_pnl_thb: float = 0.0
    best_pnl_thb: float = 0.0
    snapshot_ts: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["tier"] = self.tier.value
        return data

    @property
    def is_blocked(self) -> bool:
        return self.tier == ProposalTier.BLOCKED or bool(self.hard_blocks)


def _split_review_reasons(reasons: Iterable[str]) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    for raw in reasons:
        text = str(raw).strip()
        if not text:
            continue
        if text in SOFT_REVIEW_REASONS:
            soft.append(text)
        else:
            hard.append(text)
    return hard, soft


def assess_prune_readiness(operational_state: dict[str, Any]) -> dict[str, Any]:
    """Classify an operational-state payload into hard blocks vs. soft warnings.

    Hard-block rules (fixes the false-blocked bug):
      - Only *real* linked state (open buy/sell orders or an unresolved partial
        fill) escalates soft exchange-coverage warnings into a hard block.
      - Reserved balances without a matching open order are treated as a
        non-blocking warning ("ghost reserved") because partial exchange
        coverage can leave reserved amounts lingering without an actionable
        order to cancel.
      - Hard review reasons (ambiguous state) always block.
    """

    review_reasons = list(operational_state.get("review_reasons") or [])
    hard_reasons, soft_reasons = _split_review_reasons(review_reasons)

    open_buy = int(operational_state.get("open_buy_count", 0) or 0)
    open_sell = int(operational_state.get("open_sell_count", 0) or 0)
    reserved_thb = float(operational_state.get("reserved_thb", 0.0) or 0.0)
    reserved_coin = float(operational_state.get("reserved_coin", 0.0) or 0.0)
    partial_fill = bool(operational_state.get("partial_fill"))

    has_real_open_orders = bool(open_buy or open_sell or partial_fill)
    has_reserved = bool(reserved_thb > 0 or reserved_coin > 0)
    has_ghost_reserved = has_reserved and not has_real_open_orders

    hard_block_reasons: list[str] = []
    if partial_fill:
        hard_block_reasons.append("partial fill is still unresolved")
    hard_block_reasons.extend(hard_reasons)

    if has_real_open_orders and soft_reasons:
        hard_block_reasons.extend(soft_reasons)
        effective_soft_warnings: list[str] = []
    else:
        effective_soft_warnings = list(soft_reasons)

    if has_ghost_reserved:
        effective_soft_warnings.append(
            "reserved balance detected without a matching open order — "
            "prune is allowed but verify exchange state"
        )

    return {
        "hard_block_reasons": hard_block_reasons,
        "soft_warning_reasons": effective_soft_warnings,
        "has_linked_state": has_real_open_orders or has_reserved,
        "has_real_open_orders": has_real_open_orders,
        "has_ghost_reserved": has_ghost_reserved,
    }


def compute_rule_update_confidence(
    *,
    best_row: dict[str, Any],
    baseline_row: dict[str, Any] | None,
    freshness_status: str,
) -> float:
    """Map a compare outcome to a confidence in [0.0, 1.0]."""

    best = dict(best_row or {})
    baseline = dict(baseline_row or {})

    best_pnl = float(best.get("total_pnl_thb", 0.0) or 0.0)
    baseline_pnl = float(baseline.get("total_pnl_thb", 0.0) or 0.0)
    edge = best_pnl - baseline_pnl
    trades = int(best.get("trades", 0) or 0)
    win_rate = float(best.get("win_rate_percent", 0.0) or 0.0)
    fee_guardrail = str(best.get("fee_guardrail") or "").upper()

    score = 0.4

    if edge > 0:
        score += min(0.30, edge / 200.0)
    elif edge < 0:
        score += max(-0.30, edge / 200.0)

    if best_pnl > 0:
        score += min(0.15, best_pnl / 500.0)
    elif best_pnl < 0:
        score -= min(0.20, abs(best_pnl) / 500.0)

    if trades >= 10:
        score += 0.20
    elif trades >= 5:
        score += 0.10
    elif trades == 0:
        score -= 0.15

    if win_rate >= 65.0:
        score += 0.15
    elif win_rate >= 55.0:
        score += 0.10
    elif 0.0 < win_rate < 45.0:
        score -= 0.10

    if fee_guardrail == "FEE_OK":
        score += 0.15
    elif fee_guardrail == "FEE_HEAVY":
        score += 0.05
    elif fee_guardrail in {"THIN_EDGE", "LOSS_AFTER_FEES"}:
        score -= 0.30

    status = str(freshness_status or "").strip()
    if status == "Stale":
        score -= 0.10
    elif status == "Missing":
        score -= 0.30

    return max(0.0, min(1.0, round(score, 3)))


def classify_rule_update_tier(
    *,
    confidence: float,
    best_pnl_thb: float,
    edge_thb: float,
    fee_guardrail: str,
    freshness_status: str,
    hard_blocks: list[str],
    has_warnings: bool,
) -> ProposalTier:
    if hard_blocks:
        return ProposalTier.BLOCKED
    status = str(freshness_status or "")
    if status in {"Missing", "Stale"}:
        return ProposalTier.NEEDS_REVIEW
    if str(fee_guardrail or "").upper() == "LOSS_AFTER_FEES":
        return ProposalTier.NEEDS_REVIEW
    if best_pnl_thb <= 0.0:
        return ProposalTier.NEEDS_REVIEW
    if confidence >= 0.80 and edge_thb >= 0 and not has_warnings:
        return ProposalTier.AUTO_APPROVE
    if confidence >= 0.60:
        return ProposalTier.RECOMMENDED
    return ProposalTier.NEEDS_REVIEW


def compute_prune_confidence(
    *,
    tuning_row: dict[str, Any],
    baseline_pnl_thb: float,
    best_pnl_thb: float,
    fee_guardrail: str,
) -> float:
    score = 0.3
    row = dict(tuning_row or {})
    recommendation = str(row.get("recommendation") or "").upper()
    confidence_label = str(row.get("confidence") or "").upper()

    if recommendation == "PRUNE":
        score += 0.30
    if confidence_label == "HIGH_PRUNE":
        score += 0.20
    if baseline_pnl_thb <= 0.0 and best_pnl_thb <= 0.0:
        score += 0.20
    if str(fee_guardrail or "").upper() in {"THIN_EDGE", "LOSS_AFTER_FEES"}:
        score += 0.15

    return max(0.0, min(1.0, round(score, 3)))


def classify_prune_tier(
    *,
    confidence: float,
    hard_blocks: list[str],
) -> ProposalTier:
    if hard_blocks:
        return ProposalTier.BLOCKED
    if confidence >= 0.70:
        return ProposalTier.AUTO_APPROVE
    if confidence >= 0.50:
        return ProposalTier.RECOMMENDED
    return ProposalTier.NEEDS_REVIEW


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _expiry_iso(ttl_seconds: int) -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0)
        + timedelta(seconds=int(ttl_seconds))
    ).isoformat()


def build_rule_update_proposal(
    *,
    symbol: str,
    current_rule: dict[str, Any],
    compare_rows: list[dict[str, Any]],
    freshness_status: str,
    freshness_warning: str = "",
    ttl_seconds: int = DEFAULT_PROPOSAL_TTL_SECONDS,
) -> RuleProposal | None:
    """Turn compare rows for ONE symbol into a RuleProposal, or None if empty."""

    if not compare_rows:
        return None

    baseline = next(
        (dict(row) for row in compare_rows if str(row.get("variant") or "") == "CURRENT"),
        None,
    )
    best = next(
        (dict(row) for row in compare_rows if str(row.get("variant") or "") != "CURRENT"),
        baseline or dict(compare_rows[0]),
    )

    baseline_pnl = float((baseline or {}).get("total_pnl_thb", 0.0) or 0.0)
    best_pnl = float(best.get("total_pnl_thb", 0.0) or 0.0)
    edge = best_pnl - baseline_pnl
    fee_guardrail = str(best.get("fee_guardrail") or "")
    win_rate = float(best.get("win_rate_percent", 0.0) or 0.0)
    trades = int(best.get("trades", 0) or 0)

    confidence = compute_rule_update_confidence(
        best_row=best,
        baseline_row=baseline,
        freshness_status=freshness_status,
    )

    warnings: list[str] = []
    if freshness_warning:
        warnings.append(str(freshness_warning))
    if trades < 5:
        warnings.append(f"only {trades} replay trade(s) — sample size is small")
    if fee_guardrail in {"THIN_EDGE", "LOSS_AFTER_FEES"}:
        warnings.append(f"fee guardrail flagged {fee_guardrail}")

    hard_blocks: list[str] = []
    if str(freshness_status) == "Missing":
        hard_blocks.append("no candle data — sync required before Update Rule can run")

    tier = classify_rule_update_tier(
        confidence=confidence,
        best_pnl_thb=best_pnl,
        edge_thb=edge,
        fee_guardrail=fee_guardrail,
        freshness_status=freshness_status,
        hard_blocks=hard_blocks,
        has_warnings=bool(warnings),
    )

    reason = _build_update_reason(
        tier=tier,
        edge=edge,
        best_pnl=best_pnl,
        win_rate=win_rate,
        trades=trades,
        freshness_status=freshness_status,
    )

    return RuleProposal(
        symbol=str(symbol).strip(),
        tier=tier,
        confidence=confidence,
        current_rule=dict(current_rule or {}),
        proposed_rule=dict(best.get("rule") or {}),
        reason=reason,
        warnings=warnings,
        hard_blocks=hard_blocks,
        best_variant=str(best.get("variant") or ""),
        baseline_pnl_thb=baseline_pnl,
        proposed_pnl_thb=best_pnl,
        edge_thb=edge,
        win_rate_percent=win_rate,
        trades=trades,
        fee_guardrail=fee_guardrail,
        freshness_status=str(freshness_status),
        snapshot_ts=_now_utc_iso(),
        expires_at=_expiry_iso(ttl_seconds),
    )


def _build_update_reason(
    *,
    tier: ProposalTier,
    edge: float,
    best_pnl: float,
    win_rate: float,
    trades: int,
    freshness_status: str,
) -> str:
    if tier == ProposalTier.BLOCKED:
        return f"Blocked — {str(freshness_status).lower()} candle data"
    if tier == ProposalTier.AUTO_APPROVE:
        return (
            f"Confident uplift: {edge:+.2f} THB edge, win rate {win_rate:.1f}%, "
            f"{trades} replay trade(s)"
        )
    if tier == ProposalTier.RECOMMENDED:
        return (
            f"Positive signal: best PnL {best_pnl:+.2f} THB, "
            f"edge {edge:+.2f} THB — review before apply"
        )
    return (
        f"Weak signal: best PnL {best_pnl:+.2f} THB, edge {edge:+.2f} THB — "
        f"manual review recommended"
    )


def build_prune_proposal(
    *,
    symbol: str,
    operational_state: dict[str, Any],
    tuning_row: dict[str, Any] | None = None,
    baseline_pnl_thb: float = 0.0,
    best_pnl_thb: float = 0.0,
    fee_guardrail: str = "",
) -> PruneProposal:
    assessment = assess_prune_readiness(operational_state)
    hard_blocks = list(assessment["hard_block_reasons"])
    warnings = list(assessment["soft_warning_reasons"])

    confidence = compute_prune_confidence(
        tuning_row=tuning_row or {},
        baseline_pnl_thb=baseline_pnl_thb,
        best_pnl_thb=best_pnl_thb,
        fee_guardrail=fee_guardrail,
    )
    tier = classify_prune_tier(confidence=confidence, hard_blocks=hard_blocks)

    tuning_payload = dict(tuning_row or {})
    reason = _build_prune_reason(
        tier=tier,
        hard_blocks=hard_blocks,
        confidence=confidence,
        recommendation=str(tuning_payload.get("recommendation") or ""),
        baseline_pnl_thb=baseline_pnl_thb,
        best_pnl_thb=best_pnl_thb,
    )

    return PruneProposal(
        symbol=str(symbol).strip(),
        tier=tier,
        confidence=confidence,
        reason=reason,
        warnings=warnings,
        hard_blocks=hard_blocks,
        open_buy_count=int(operational_state.get("open_buy_count", 0) or 0),
        open_sell_count=int(operational_state.get("open_sell_count", 0) or 0),
        reserved_thb=float(operational_state.get("reserved_thb", 0.0) or 0.0),
        reserved_coin=float(operational_state.get("reserved_coin", 0.0) or 0.0),
        partial_fill=bool(operational_state.get("partial_fill")),
        has_real_open_orders=bool(assessment["has_real_open_orders"]),
        has_ghost_reserved=bool(assessment["has_ghost_reserved"]),
        tuning_recommendation=str(tuning_payload.get("recommendation") or ""),
        baseline_pnl_thb=baseline_pnl_thb,
        best_pnl_thb=best_pnl_thb,
        snapshot_ts=_now_utc_iso(),
    )


def _build_prune_reason(
    *,
    tier: ProposalTier,
    hard_blocks: list[str],
    confidence: float,
    recommendation: str,
    baseline_pnl_thb: float,
    best_pnl_thb: float,
) -> str:
    if tier == ProposalTier.BLOCKED:
        return "Blocked — " + "; ".join(hard_blocks)
    parts: list[str] = []
    if recommendation == "PRUNE":
        parts.append("live tuning flags PRUNE")
    if baseline_pnl_thb <= 0.0 and best_pnl_thb <= 0.0:
        parts.append(
            f"non-profitable (baseline {baseline_pnl_thb:+.2f}, best {best_pnl_thb:+.2f} THB)"
        )
    if not parts:
        parts.append(f"confidence {confidence:.2f}")
    return "; ".join(parts)


def group_proposals_by_tier(
    proposals: Iterable[RuleProposal | PruneProposal],
) -> dict[str, list[Any]]:
    buckets: dict[str, list[Any]] = {tier.value: [] for tier in ProposalTier}
    for proposal in proposals:
        buckets[proposal.tier.value].append(proposal)
    return buckets


def summarize_proposal_counts(
    proposals: Iterable[RuleProposal | PruneProposal],
) -> dict[str, int]:
    counts = {tier.value: 0 for tier in ProposalTier}
    for proposal in proposals:
        counts[proposal.tier.value] += 1
    return counts
