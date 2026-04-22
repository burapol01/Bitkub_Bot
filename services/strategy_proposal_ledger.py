"""Persistence ledger for Strategy Inbox proposals (Phase 2).

Stores every proposal the Inbox generates with a stable id so that:
  * Repeated recomputes within a short window are idempotent (dedup).
  * Lifecycle transitions (pending -> applied/dismissed/expired/superseded)
    are authoritative outside Streamlit's session state.
  * Every user decision leaves an append-only audit row.

Thin over ``services.db_service`` — all SQL is local to this module so the
Inbox UI can stay dumb.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from services import db_service
from services.strategy_proposal_service import (
    DEFAULT_SNAPSHOT_BUCKET_SECONDS,
    ProposalKind,
    ProposalTier,
    PruneProposal,
    RuleProposal,
    rule_hash,
    stable_proposal_id,
)


DEFAULT_DISMISSAL_SECONDS = 6 * 60 * 60  # 6 hours


class ProposalStatus(str):
    PENDING = "pending"
    APPLIED = "applied"
    DISMISSED = "dismissed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


TERMINAL_STATUSES: frozenset[str] = frozenset(
    {ProposalStatus.APPLIED, ProposalStatus.EXPIRED, ProposalStatus.SUPERSEDED}
)


class LedgerError(RuntimeError):
    """Raised on illegal lifecycle transitions or unknown proposals."""


@dataclass(frozen=True)
class UpsertResult:
    persisted: list[str]
    deduped: list[str]
    suppressed: list[str]
    superseded: list[str]


@dataclass
class LedgerRow:
    proposal_id: str
    symbol: str
    kind: str
    tier: str
    confidence: float
    rule_hash: str
    payload: dict[str, Any]
    snapshot_ts: str
    expires_at: str | None
    status: str
    status_updated_at: str
    dismissed_until: str | None
    resolution: str | None
    lookback_days: int | None
    created_at: str
    updated_at: str

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = _parse_utc(self.expires_at)
        except ValueError:
            return True
        return _as_utc(now) >= exp


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(str(ts))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _proposal_as_dict(proposal: RuleProposal | PruneProposal) -> dict[str, Any]:
    return proposal.as_dict()


def _proposal_kind(proposal: RuleProposal | PruneProposal) -> str:
    if isinstance(proposal, RuleProposal):
        return ProposalKind.RULE_UPDATE.value
    if isinstance(proposal, PruneProposal):
        return ProposalKind.PRUNE.value
    raise LedgerError(f"unsupported proposal type: {type(proposal).__name__}")


def _proposal_rule_hash(proposal: RuleProposal | PruneProposal) -> str:
    if isinstance(proposal, RuleProposal):
        return rule_hash(proposal.proposed_rule)
    # Prune proposals have no rule payload — hash the action intent so that
    # toggling ``remove_from_watchlist`` still dedups to the same row.
    intent = {
        "action": "PRUNE",
        "remove_from_watchlist": bool(getattr(proposal, "remove_from_watchlist", False)),
    }
    return rule_hash(intent)


def _row_to_ledger(row: sqlite3.Row) -> LedgerRow:
    return LedgerRow(
        proposal_id=row["proposal_id"],
        symbol=row["symbol"],
        kind=row["kind"],
        tier=row["tier"],
        confidence=float(row["confidence"]),
        rule_hash=row["rule_hash"],
        payload=json.loads(row["payload_json"] or "{}"),
        snapshot_ts=row["snapshot_ts"],
        expires_at=row["expires_at"],
        status=row["status"],
        status_updated_at=row["status_updated_at"],
        dismissed_until=row["dismissed_until"],
        resolution=row["resolution"],
        lookback_days=row["lookback_days"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def upsert_pending(
    proposals: Iterable[RuleProposal | PruneProposal],
    *,
    resolution: str | None = None,
    lookback_days: int | None = None,
    bucket_seconds: int = DEFAULT_SNAPSHOT_BUCKET_SECONDS,
    now: datetime | None = None,
) -> UpsertResult:
    """Persist a batch of proposals as pending rows.

    Behaviour:
      * Same ``(symbol, kind, rule_hash)`` within the same bucket -> id collision,
        INSERT OR IGNORE; caller sees it in ``deduped``.
      * Active dismissed row (``dismissed_until > now``) with same rule_hash ->
        suppressed (not persisted); caller sees it in ``suppressed``.
      * Different rule_hash for the same ``(symbol, kind)`` with open pending
        rows -> those open rows are transitioned to ``superseded`` and the new
        row is persisted.
    """

    reference = _as_utc(now)
    now_iso = _iso(reference)
    persisted: list[str] = []
    deduped: list[str] = []
    suppressed: list[str] = []
    superseded: list[str] = []

    proposals = list(proposals)
    if not proposals:
        return UpsertResult([], [], [], [])

    with db_service._connect() as conn:
        for proposal in proposals:
            kind = _proposal_kind(proposal)
            rhash = _proposal_rule_hash(proposal)
            snapshot_ts = getattr(proposal, "snapshot_ts", "") or now_iso
            proposal_id = stable_proposal_id(
                symbol=proposal.symbol,
                kind=kind,
                rule_hash_value=rhash,
                snapshot_ts=snapshot_ts,
                bucket_seconds=bucket_seconds,
            )

            if _is_suppressed(
                conn,
                symbol=proposal.symbol,
                kind=kind,
                rule_hash_value=rhash,
                now_iso=now_iso,
            ):
                suppressed.append(proposal_id)
                continue

            supersede_ids = _supersede_conflicting(
                conn,
                symbol=proposal.symbol,
                kind=kind,
                keep_rule_hash=rhash,
                now_iso=now_iso,
            )
            superseded.extend(supersede_ids)

            expires_at = getattr(proposal, "expires_at", "") or None
            payload_json = json.dumps(_proposal_as_dict(proposal), ensure_ascii=True, default=str)
            tier_value = (
                proposal.tier.value
                if isinstance(proposal.tier, ProposalTier)
                else str(proposal.tier)
            )

            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO strategy_proposals (
                    proposal_id, symbol, kind, tier, confidence, rule_hash,
                    payload_json, snapshot_ts, expires_at, status,
                    status_updated_at, dismissed_until, resolution,
                    lookback_days, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    proposal.symbol,
                    kind,
                    tier_value,
                    float(proposal.confidence),
                    rhash,
                    payload_json,
                    snapshot_ts,
                    expires_at,
                    ProposalStatus.PENDING,
                    now_iso,
                    resolution,
                    lookback_days,
                    now_iso,
                    now_iso,
                ),
            )
            if cursor.rowcount == 0:
                deduped.append(proposal_id)
                continue

            _insert_decision(
                conn,
                proposal_id=proposal_id,
                decided_at=now_iso,
                decision="created",
                actor_type="system",
                actor_id=None,
                reason=None,
                metadata={"tier": tier_value, "rule_hash": rhash},
            )
            persisted.append(proposal_id)

    return UpsertResult(
        persisted=persisted,
        deduped=deduped,
        suppressed=suppressed,
        superseded=superseded,
    )


def list_active(
    *,
    kind: str | None = None,
    now: datetime | None = None,
    sweep: bool = True,
) -> list[LedgerRow]:
    """Return pending rows that have not expired.

    If ``sweep`` is True, pending rows whose ``expires_at`` has passed are
    atomically transitioned to ``expired`` before the result is returned.
    """

    reference = _as_utc(now)
    now_iso = _iso(reference)

    if sweep:
        sweep_expired(now=reference)

    query = [
        "SELECT * FROM strategy_proposals WHERE status = ?",
    ]
    params: list[Any] = [ProposalStatus.PENDING]
    if kind:
        query.append("AND kind = ?")
        params.append(str(kind))
    query.append("ORDER BY snapshot_ts DESC, proposal_id")

    with db_service._connect() as conn:
        rows = conn.execute(" ".join(query), tuple(params)).fetchall()

    return [_row_to_ledger(row) for row in rows]


def get(proposal_id: str) -> LedgerRow | None:
    with db_service._connect() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_proposals WHERE proposal_id = ?",
            (str(proposal_id),),
        ).fetchone()
    return _row_to_ledger(row) if row else None


def mark_applied(
    proposal_id: str,
    *,
    actor_id: str | None,
    metadata: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> LedgerRow:
    reference = _as_utc(now)
    now_iso = _iso(reference)

    with db_service._connect() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_proposals WHERE proposal_id = ?",
            (str(proposal_id),),
        ).fetchone()
        if row is None:
            raise LedgerError(f"unknown proposal_id: {proposal_id}")
        if row["status"] in TERMINAL_STATUSES:
            raise LedgerError(
                f"proposal {proposal_id} already terminal ({row['status']})"
            )
        if row["status"] == ProposalStatus.DISMISSED:
            raise LedgerError(
                f"proposal {proposal_id} is dismissed — reopen before applying"
            )
        expires_at = row["expires_at"]
        if expires_at and _parse_utc(expires_at) <= reference:
            conn.execute(
                """
                UPDATE strategy_proposals
                SET status = ?, status_updated_at = ?, updated_at = ?
                WHERE proposal_id = ?
                """,
                (ProposalStatus.EXPIRED, now_iso, now_iso, proposal_id),
            )
            _insert_decision(
                conn,
                proposal_id=proposal_id,
                decided_at=now_iso,
                decision="expired",
                actor_type="system",
                actor_id=None,
                reason="TTL elapsed at apply time",
                metadata=None,
            )
            conn.commit()  # Persist expiry before surfacing the error.
            raise LedgerError(
                f"proposal {proposal_id} expired at {expires_at} — recompute required"
            )

        conn.execute(
            """
            UPDATE strategy_proposals
            SET status = ?, status_updated_at = ?, updated_at = ?
            WHERE proposal_id = ?
            """,
            (ProposalStatus.APPLIED, now_iso, now_iso, proposal_id),
        )
        _insert_decision(
            conn,
            proposal_id=proposal_id,
            decided_at=now_iso,
            decision="applied",
            actor_type="user",
            actor_id=actor_id,
            reason=None,
            metadata=metadata,
        )
        updated = conn.execute(
            "SELECT * FROM strategy_proposals WHERE proposal_id = ?",
            (str(proposal_id),),
        ).fetchone()

    return _row_to_ledger(updated)


def mark_dismissed(
    proposal_id: str,
    *,
    actor_id: str | None,
    reason: str | None = None,
    dismissal_seconds: int = DEFAULT_DISMISSAL_SECONDS,
    now: datetime | None = None,
) -> LedgerRow:
    reference = _as_utc(now)
    now_iso = _iso(reference)
    until = _iso(reference + timedelta(seconds=max(0, int(dismissal_seconds))))

    with db_service._connect() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_proposals WHERE proposal_id = ?",
            (str(proposal_id),),
        ).fetchone()
        if row is None:
            raise LedgerError(f"unknown proposal_id: {proposal_id}")
        if row["status"] in TERMINAL_STATUSES:
            raise LedgerError(
                f"proposal {proposal_id} already terminal ({row['status']})"
            )

        conn.execute(
            """
            UPDATE strategy_proposals
            SET status = ?, status_updated_at = ?, dismissed_until = ?, updated_at = ?
            WHERE proposal_id = ?
            """,
            (ProposalStatus.DISMISSED, now_iso, until, now_iso, proposal_id),
        )
        _insert_decision(
            conn,
            proposal_id=proposal_id,
            decided_at=now_iso,
            decision="dismissed",
            actor_type="user",
            actor_id=actor_id,
            reason=reason,
            metadata={"dismissed_until": until},
        )
        updated = conn.execute(
            "SELECT * FROM strategy_proposals WHERE proposal_id = ?",
            (str(proposal_id),),
        ).fetchone()

    return _row_to_ledger(updated)


def sweep_expired(*, now: datetime | None = None) -> list[str]:
    reference = _as_utc(now)
    now_iso = _iso(reference)

    with db_service._connect() as conn:
        rows = conn.execute(
            """
            SELECT proposal_id FROM strategy_proposals
            WHERE status = ? AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (ProposalStatus.PENDING, now_iso),
        ).fetchall()
        expired_ids = [row["proposal_id"] for row in rows]
        if not expired_ids:
            return []
        conn.executemany(
            """
            UPDATE strategy_proposals
            SET status = ?, status_updated_at = ?, updated_at = ?
            WHERE proposal_id = ? AND status = ?
            """,
            [
                (
                    ProposalStatus.EXPIRED,
                    now_iso,
                    now_iso,
                    pid,
                    ProposalStatus.PENDING,
                )
                for pid in expired_ids
            ],
        )
        conn.executemany(
            """
            INSERT INTO strategy_proposal_decisions (
                proposal_id, decided_at, decision, actor_type, actor_id,
                reason, metadata_json
            ) VALUES (?, ?, ?, ?, NULL, ?, NULL)
            """,
            [
                (pid, now_iso, "expired", "system", "TTL elapsed")
                for pid in expired_ids
            ],
        )
    return expired_ids


def is_suppressed(
    *,
    symbol: str,
    kind: str,
    rule_hash_value: str,
    now: datetime | None = None,
) -> bool:
    reference = _as_utc(now)
    with db_service._connect() as conn:
        return _is_suppressed(
            conn,
            symbol=symbol,
            kind=kind,
            rule_hash_value=rule_hash_value,
            now_iso=_iso(reference),
        )


def list_decisions(
    proposal_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with db_service._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, proposal_id, decided_at, decision, actor_type, actor_id,
                   reason, metadata_json
            FROM strategy_proposal_decisions
            WHERE proposal_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (str(proposal_id), int(limit)),
        ).fetchall()
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
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Internal helpers (operate on an open sqlite connection)
# ---------------------------------------------------------------------------


def _is_suppressed(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    kind: str,
    rule_hash_value: str,
    now_iso: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM strategy_proposals
        WHERE symbol = ? AND kind = ? AND rule_hash = ?
          AND status = ? AND dismissed_until IS NOT NULL
          AND dismissed_until > ?
        LIMIT 1
        """,
        (
            str(symbol),
            str(kind),
            str(rule_hash_value),
            ProposalStatus.DISMISSED,
            now_iso,
        ),
    ).fetchone()
    return row is not None


def _supersede_conflicting(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    kind: str,
    keep_rule_hash: str,
    now_iso: str,
) -> list[str]:
    rows = conn.execute(
        """
        SELECT proposal_id FROM strategy_proposals
        WHERE symbol = ? AND kind = ? AND status = ? AND rule_hash != ?
        """,
        (
            str(symbol),
            str(kind),
            ProposalStatus.PENDING,
            str(keep_rule_hash),
        ),
    ).fetchall()
    ids = [row["proposal_id"] for row in rows]
    if not ids:
        return []
    conn.executemany(
        """
        UPDATE strategy_proposals
        SET status = ?, status_updated_at = ?, updated_at = ?
        WHERE proposal_id = ? AND status = ?
        """,
        [
            (
                ProposalStatus.SUPERSEDED,
                now_iso,
                now_iso,
                pid,
                ProposalStatus.PENDING,
            )
            for pid in ids
        ],
    )
    conn.executemany(
        """
        INSERT INTO strategy_proposal_decisions (
            proposal_id, decided_at, decision, actor_type, actor_id,
            reason, metadata_json
        ) VALUES (?, ?, ?, ?, NULL, ?, NULL)
        """,
        [
            (pid, now_iso, "superseded", "system", "replaced by newer proposal")
            for pid in ids
        ],
    )
    return ids


def _insert_decision(
    conn: sqlite3.Connection,
    *,
    proposal_id: str,
    decided_at: str,
    decision: str,
    actor_type: str,
    actor_id: str | None,
    reason: str | None,
    metadata: dict[str, Any] | None,
) -> None:
    conn.execute(
        """
        INSERT INTO strategy_proposal_decisions (
            proposal_id, decided_at, decision, actor_type, actor_id,
            reason, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(proposal_id),
            decided_at,
            decision,
            actor_type,
            actor_id,
            reason,
            json.dumps(metadata, ensure_ascii=True) if metadata else None,
        ),
    )


__all__ = [
    "DEFAULT_DISMISSAL_SECONDS",
    "LedgerError",
    "LedgerRow",
    "ProposalStatus",
    "TERMINAL_STATUSES",
    "UpsertResult",
    "get",
    "is_suppressed",
    "list_active",
    "list_decisions",
    "mark_applied",
    "mark_dismissed",
    "sweep_expired",
    "upsert_pending",
]
