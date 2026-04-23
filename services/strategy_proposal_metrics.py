"""Aggregate metrics for the Strategy Proposal ledger.

Provides a read-only view over ``strategy_proposals`` and
``strategy_proposal_decisions`` so the Inbox can surface:

  * counts by status / kind / tier (lifetime and recent window)
  * apply / dismissal rates within a rolling window
  * average time-to-decision across user decisions
  * the most recent decision rows for at-a-glance auditability

All aggregation happens in SQL on the active DB connection — no Python-side
loops over the full ledger.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from services import db_service
from services.strategy_proposal_ledger import ProposalStatus
from services.strategy_proposal_service import ProposalKind, ProposalTier


DEFAULT_WINDOW_HOURS = 24
DEFAULT_RECENT_DECISIONS_LIMIT = 10
USER_DECISIONS: frozenset[str] = frozenset({"applied", "dismissed"})
TERMINAL_DECISIONS: frozenset[str] = frozenset(
    {"applied", "dismissed", "expired", "superseded"}
)


@dataclass(frozen=True)
class LedgerSummary:
    counts_by_status: dict[str, int]
    counts_by_kind: dict[str, int]
    counts_by_tier: dict[str, int]
    window_counts_by_decision: dict[str, int]
    apply_rate: float
    dismissal_rate: float
    avg_time_to_decision_seconds: float | None
    recent_decisions: list[dict[str, Any]]
    window_hours: int
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _zero_status_bucket() -> dict[str, int]:
    return {
        ProposalStatus.PENDING: 0,
        ProposalStatus.APPLIED: 0,
        ProposalStatus.DISMISSED: 0,
        ProposalStatus.EXPIRED: 0,
        ProposalStatus.SUPERSEDED: 0,
    }


def _zero_kind_bucket() -> dict[str, int]:
    return {
        ProposalKind.RULE_UPDATE.value: 0,
        ProposalKind.PRUNE.value: 0,
    }


def _zero_tier_bucket() -> dict[str, int]:
    return {
        ProposalTier.AUTO_APPROVE.value: 0,
        ProposalTier.RECOMMENDED.value: 0,
        ProposalTier.NEEDS_REVIEW.value: 0,
        ProposalTier.BLOCKED.value: 0,
    }


def compute_ledger_summary(
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    recent_decisions_limit: int = DEFAULT_RECENT_DECISIONS_LIMIT,
    now: datetime | None = None,
) -> LedgerSummary:
    """Collect lifetime + windowed metrics from the proposal ledger."""

    reference = _as_utc(now)
    window = max(0, int(window_hours))
    window_start_iso = _iso(reference - timedelta(hours=window))

    with db_service._connect() as conn:
        counts_by_status = _zero_status_bucket()
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM strategy_proposals GROUP BY status"
        ):
            counts_by_status[row["status"]] = int(row["n"])

        counts_by_kind = _zero_kind_bucket()
        for row in conn.execute(
            "SELECT kind, COUNT(*) AS n FROM strategy_proposals GROUP BY kind"
        ):
            counts_by_kind[row["kind"]] = int(row["n"])

        counts_by_tier = _zero_tier_bucket()
        for row in conn.execute(
            "SELECT tier, COUNT(*) AS n FROM strategy_proposals GROUP BY tier"
        ):
            counts_by_tier[row["tier"]] = int(row["n"])

        window_counts_by_decision: dict[str, int] = {d: 0 for d in TERMINAL_DECISIONS}
        for row in conn.execute(
            """
            SELECT decision, COUNT(*) AS n
            FROM strategy_proposal_decisions
            WHERE decided_at >= ?
            GROUP BY decision
            """,
            (window_start_iso,),
        ):
            decision = row["decision"]
            if decision in window_counts_by_decision:
                window_counts_by_decision[decision] = int(row["n"])

        decision_rows = conn.execute(
            """
            SELECT d.proposal_id, d.decided_at, d.decision, d.actor_type,
                   d.actor_id, d.reason, p.symbol, p.kind, p.tier
            FROM strategy_proposal_decisions d
            LEFT JOIN strategy_proposals p ON p.proposal_id = d.proposal_id
            ORDER BY d.id DESC
            LIMIT ?
            """,
            (int(max(0, recent_decisions_limit)),),
        ).fetchall()

        avg_seconds_row = conn.execute(
            """
            SELECT AVG(
                (julianday(d.decided_at) - julianday(p.created_at)) * 86400.0
            ) AS avg_seconds
            FROM strategy_proposal_decisions d
            JOIN strategy_proposals p ON p.proposal_id = d.proposal_id
            WHERE d.decision IN ('applied', 'dismissed')
              AND d.decided_at >= ?
            """,
            (window_start_iso,),
        ).fetchone()

    applied = int(window_counts_by_decision.get("applied", 0))
    dismissed = int(window_counts_by_decision.get("dismissed", 0))
    expired = int(window_counts_by_decision.get("expired", 0))
    superseded = int(window_counts_by_decision.get("superseded", 0))
    denom = applied + dismissed + expired + superseded
    apply_rate = (applied / denom) if denom else 0.0
    dismissal_rate = (dismissed / denom) if denom else 0.0

    avg_seconds: float | None = None
    if avg_seconds_row is not None:
        raw_avg = avg_seconds_row["avg_seconds"]
        avg_seconds = float(raw_avg) if raw_avg is not None else None

    recent_decisions = [
        {
            "proposal_id": row["proposal_id"],
            "decided_at": row["decided_at"],
            "decision": row["decision"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "reason": row["reason"],
            "symbol": row["symbol"],
            "kind": row["kind"],
            "tier": row["tier"],
        }
        for row in decision_rows
    ]

    return LedgerSummary(
        counts_by_status=counts_by_status,
        counts_by_kind=counts_by_kind,
        counts_by_tier=counts_by_tier,
        window_counts_by_decision=window_counts_by_decision,
        apply_rate=round(apply_rate, 4),
        dismissal_rate=round(dismissal_rate, 4),
        avg_time_to_decision_seconds=avg_seconds,
        recent_decisions=recent_decisions,
        window_hours=window,
        generated_at=_iso(reference),
    )


def list_recent_decisions(
    *,
    limit: int = 50,
    since: datetime | str | None = None,
    kind: str | None = None,
    symbol: str | None = None,
    decision: str | None = None,
    proposal_id: str | None = None,
) -> list[dict[str, Any]]:
    """Flexible audit-log query over ``strategy_proposal_decisions``.

    Joins proposal metadata so callers can filter by symbol/kind without a
    second round trip. All filters are optional; ``limit`` caps the result
    size. Results are ordered newest first.
    """

    clauses: list[str] = []
    params: list[Any] = []

    if since is not None:
        if isinstance(since, datetime):
            since_iso = _iso(_as_utc(since))
        else:
            since_iso = str(since)
        clauses.append("d.decided_at >= ?")
        params.append(since_iso)
    if kind:
        clauses.append("p.kind = ?")
        params.append(str(kind))
    if symbol:
        clauses.append("p.symbol = ?")
        params.append(str(symbol))
    if decision:
        clauses.append("d.decision = ?")
        params.append(str(decision))
    if proposal_id:
        clauses.append("d.proposal_id = ?")
        params.append(str(proposal_id))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT d.id, d.proposal_id, d.decided_at, d.decision, d.actor_type,
               d.actor_id, d.reason, d.metadata_json,
               p.symbol, p.kind, p.tier, p.status
        FROM strategy_proposal_decisions d
        LEFT JOIN strategy_proposals p ON p.proposal_id = d.proposal_id
        {where}
        ORDER BY d.id DESC
        LIMIT ?
    """

    params.append(int(max(0, limit)))

    with db_service._connect() as conn:
        rows = conn.execute(query, tuple(params)).fetchall()

    import json

    return [
        {
            "id": row["id"],
            "proposal_id": row["proposal_id"],
            "decided_at": row["decided_at"],
            "decision": row["decision"],
            "actor_type": row["actor_type"],
            "actor_id": row["actor_id"],
            "reason": row["reason"],
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            "symbol": row["symbol"],
            "kind": row["kind"],
            "tier": row["tier"],
            "status": row["status"],
        }
        for row in rows
    ]


__all__ = [
    "DEFAULT_RECENT_DECISIONS_LIMIT",
    "DEFAULT_WINDOW_HOURS",
    "LedgerSummary",
    "TERMINAL_DECISIONS",
    "USER_DECISIONS",
    "compute_ledger_summary",
    "list_recent_decisions",
]
