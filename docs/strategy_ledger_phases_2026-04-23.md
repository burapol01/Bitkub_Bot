# Strategy Proposal Ledger — Phases 2–6 Summary (2026-04-23)

สรุปงานฝั่ง Strategy Proposal Ledger ที่ merge เข้า `main` แล้วในวันที่ 2026-04-23 ต่อจากเอกสาร [strategy_post_merge_summary_2026-04-22.md](./strategy_post_merge_summary_2026-04-22.md) เพื่อให้คนมาอ่านต่อเห็น state flow ใหม่, lifecycle ของ proposal, และหน้า Strategy Inbox หลัง rollout รอบนี้

ช่วงที่ครอบคลุม:

| PR | Commit | Date | Phase | Summary |
| --- | --- | --- | --- | --- |
| #64 | `4d7ed31` | 2026-04-22 | Phase 2 | proposal ledger persistence (tables + lifecycle API) |
| #64 | `29ee5d1` | 2026-04-22 | Phase 2 | harden ledger tests against wall-clock sweep |
| #65 | `eb4a82c` | 2026-04-22 | Phase 3 | wire Strategy Inbox UI to ledger as source of truth |
| #66 | `baffd8c` | 2026-04-23 | Phase 4 | throttled startup sweep + CLI runner for expired proposals |
| #67 | `ae5cf4c` | 2026-04-23 | Phase 5 | ledger observability metrics panel in Inbox |
| #68 | `bb0957d` | 2026-04-23 | Phase 6 | decision audit log viewer in Inbox |

Test count grew from 185 → 210 across these phases. Full suite remains green.

## 1. Phase 2 — Ledger persistence

PR #64 ได้เพิ่ม 2 ตารางใหม่ใน [services/db_service.py](../services/db_service.py) และ service layer ใหม่ [services/strategy_proposal_ledger.py](../services/strategy_proposal_ledger.py) เพื่อให้ proposal ที่หน้า Inbox สร้างออกมา มี authoritative store อยู่ใน SQLite ไม่ต้องพึ่ง `st.session_state`

### Schema

- `strategy_proposals` — หนึ่ง row ต่อ proposal ที่ยังมีชีวิต โดยมีคอลัมน์หลัก:
  - `proposal_id` (PK) — stable sha1 ของ `(symbol, kind, rule_hash, bucket)`
  - `kind` — `RULE_UPDATE` หรือ `PRUNE`
  - `tier` — `AUTO_APPROVE` / `RECOMMENDED` / `NEEDS_REVIEW` / `BLOCKED`
  - `status` — lifecycle state (ดูด้านล่าง)
  - `payload_json` — snapshot ของ dataclass ที่ Inbox ใช้ render
  - `snapshot_ts`, `expires_at`, `dismissed_until` — เวลาสำหรับ TTL / suppression
- `strategy_proposal_decisions` — append-only audit log ทุก decision (`created`, `applied`, `dismissed`, `expired`, `superseded`) พร้อม `actor_type`, `actor_id`, `metadata_json`

ทั้งสองตารางมี index สำหรับ query หลัก:
- `idx_strategy_proposals_status_kind(status, kind, snapshot_ts DESC)`
- `idx_strategy_proposals_symbol_kind(symbol, kind, status)`
- `idx_strategy_proposals_dismissed(symbol, kind, rule_hash, dismissed_until)`
- `idx_strategy_proposals_expires_at(status, expires_at)`
- `idx_strategy_proposal_decisions_proposal(proposal_id, id DESC)`

### Stable proposal id + bucket dedup

- `rule_hash(rule)` = sha1 ของ rule dict ที่ canonicalise ด้วย `sort_keys=True` ให้ lookup เสถียรไม่สน insertion order
- `stable_proposal_id(symbol, kind, rule_hash, snapshot_ts, bucket_seconds=300)` — bucket timestamp เป็นช่วงละ 5 นาที
- ผลคือ recompute ซ้ำภายใน 5 นาทีด้วย logical rule ตัวเดิมจะ collapse เป็น `proposal_id` เดียว ทำให้ `INSERT OR IGNORE` เป็น idempotent

### Lifecycle

```
          upsert_pending           mark_applied
created ───────────────► pending ───────────────► applied
                          │
                          ├─── mark_dismissed ────► dismissed
                          │
                          ├─── sweep_expired ─────► expired
                          │
                          └─── supersede (new) ───► superseded
```

- `upsert_pending(proposals, now=...)`:
  - dedup (same bucket) → `deduped`
  - dismissed row with same rule_hash ยังอยู่ในหน้าต่าง → `suppressed`
  - open pending ของ (symbol, kind) แต่ rule_hash ต่าง → ของเก่าโดน `superseded`
- `mark_applied(proposal_id, actor_id, metadata=None, now=None)`:
  - reject ถ้า terminal / dismissed / หมด TTL (expires_at ≤ now)
  - หมด TTL ระหว่าง apply → flip เป็น expired + raise `LedgerError`
- `mark_dismissed(proposal_id, actor_id, reason=None, dismissal_seconds=6*3600, now=None)` — ตั้ง `dismissed_until` สำหรับ suppression รอบถัดไป
- `sweep_expired(now)` — batch flip pending → expired สำหรับทุก row ที่ `expires_at ≤ now`

### Post-mortem: wall-clock sweep bleed

commit `29ee5d1` แก้ test flakiness หลัง merge: `list_active()` auto-sweep ใช้ wall clock เป็น default ทำให้ test ที่ freeze `now` ตอน upsert แต่เรียก `list_active()` โดยไม่ส่ง `now` เห็นผลของ real time ที่เลย `expires_at` ของ fixture แล้ว

fix: thread `now=now` ให้กับทุก `list_active(...)` ใน `tests/test_strategy_proposal_ledger.py` เพื่อให้ hermetic — service semantics ไม่เปลี่ยน TTL / dismissal / lifecycle

## 2. Phase 3 — UI wired to ledger

PR #65 ย้าย Strategy Inbox ให้ใช้ ledger เป็น source of truth แทน `st.session_state`

### State flow before → after

Before — session-state-backed:

```
Refresh → recompute_proposals() → st.session_state[INBOX_STATE_KEY] = full snapshot
Render   ← st.session_state["updates"] / ["prunes"]
Apply    → save_config_with_feedback() + mutate st.session_state snapshot
Refresh page → session empty → user must recompute again
```

After — ledger-backed:

```
Refresh → recompute_proposals() → persist_recompute_to_ledger() (upsert_pending)
         st.session_state[INBOX_STATE_KEY] = { snapshot_ts, resolution, lookback_days, skipped, ranking_errors }
Render   ← load_active_inbox() → ledger.list_active(kind=RULE_UPDATE|PRUNE)
Apply    → save_config_with_feedback() + ledger.mark_applied(proposal_id)
Dismiss  → ledger.mark_dismissed(proposal_id)
Refresh page → session metadata lost, proposals survive in DB
```

### Testable seams in [ui/streamlit/strategy_inbox.py](../ui/streamlit/strategy_inbox.py)

Extracted pure helpers จาก Streamlit side-effects เพื่อให้ test ตรง ๆ ได้:

- `persist_recompute_to_ledger(snapshot, *, resolution, lookback_days, now=None)` — เขียน pending rows
- `load_active_inbox(*, now=None)` — อ่าน active rows แล้ว reconstruct dataclass
- `apply_rule_update_action(config, proposals, selected_symbols, save_config_fn=..., mark_applied_fn=..., now=None)` — ปิด config save + ledger mark_applied
- `apply_prune_action(config, proposals, selected_symbols, remove_watchlist, save_config_fn=..., now=None)`
- `dismiss_proposals_action(proposals, selected_symbols, mark_dismissed_fn=..., now=None)`

ทุกตัวรับ injectable `save_config_fn`, `mark_*_fn`, `now` เพื่อ test ได้โดยไม่ต้อง spin Streamlit

### Dataclass change

เพิ่ม optional field `proposal_id: str = ""` ให้กับ `RuleProposal` และ `PruneProposal` ใน [services/strategy_proposal_service.py](../services/strategy_proposal_service.py) — default ว่าง เพื่อ backward-compat ตอน build ใหม่ แต่ `load_active_inbox` จะ override จาก `LedgerRow.proposal_id` จริง

## 3. Phase 4 — Throttled sweep + CLI

PR #66 เพิ่ม wrapper `run_startup_sweep` ให้ `sweep_expired` เพื่อให้ caller แบบถี่ ๆ (Streamlit rerun) ไม่ hammer DB

### Service API

```python
run_startup_sweep(
    *,
    min_interval_seconds: int = 60,
    now: datetime | None = None,
    last_sweep_at: datetime | str | None = None,
) -> {
    "skipped": bool,
    "expired_ids": list[str],
    "last_sweep_at": datetime,
}
```

- รับ `last_sweep_at` เป็น `datetime` หรือ ISO string ก็ได้
- ถ้า `elapsed < min_interval_seconds` → return `skipped=True` โดยไม่แตะ DB
- มิฉะนั้นเรียก `sweep_expired(now=reference)` แล้วคืน `expired_ids`

### App hook

[ui/streamlit/app.py](../ui/streamlit/app.py) มี `_run_ledger_sweep()` หลัง `init_db()` — ใช้ `st.session_state[LEDGER_SWEEP_STATE_KEY]` เก็บ `last_sweep_at` ให้ throttle ทำงานข้าม rerun ของ session เดียวกัน

ข้อเสียที่รู้ตัว: throttle เป็น per-session (ไม่ใช่ global) แปลว่าหลาย browser sessions = หลายครั้ง sweep แต่ `sweep_expired` idempotent อยู่แล้ว ไม่เป็นปัญหา

### CLI runner

[scripts/sweep_proposals.py](../scripts/sweep_proposals.py) สำหรับ cron/systemd timer:

```bash
python scripts/sweep_proposals.py --min-interval-seconds 0
# => swept N expired proposal(s)
```

- ไม่ต้องรอ Streamlit เปิด
- `--min-interval-seconds 0` ให้ cron สั่ง sweep จริงเสมอ (ปล่อยให้ cron schedule เป็นตัวคุม interval)

## 4. Phase 5 — Metrics panel

PR #67 เพิ่ม service layer ใหม่ [services/strategy_proposal_metrics.py](../services/strategy_proposal_metrics.py) ที่ aggregate จาก `strategy_proposals` และ `strategy_proposal_decisions` ใน SQL ทั้งหมด (ไม่วน Python-side)

### `compute_ledger_summary(window_hours=24, now=None) -> LedgerSummary`

คืน dataclass ที่รวม:
- `counts_by_status` — lifetime count ต่อ status
- `counts_by_kind` — lifetime count ต่อ kind
- `counts_by_tier` — lifetime count ต่อ tier
- `window_counts_by_decision` — นับ decision ภายใน window (ตัวหารของ rate)
- `apply_rate`, `dismissal_rate` — `applied / (applied + dismissed + expired + superseded)` ฯลฯ ใน window เดียวกัน
- `avg_time_to_decision_seconds` — avg ของ `julianday(decided_at) - julianday(created_at)` สำหรับ user decisions ใน window
- `recent_decisions` — 10 decision ล่าสุดพร้อม join proposal metadata

### UI panel

Strategy Inbox แสดง expander `Inbox metrics (last 24h)` ที่:
- metric card 4 ตัว: Applied / Dismissed / Expired / Avg time-to-decision
- caption lifetime status counts
- caption recent decisions ล่าสุด 10 รายการ

Exception ใน metrics ไม่ block หน้า — จับเป็น caption `Ledger metrics unavailable: {exc}` แทน

## 5. Phase 6 — Decision audit log viewer

PR #68 เพิ่ม `list_recent_decisions` ใน `services/strategy_proposal_metrics.py` และ expander ใหม่ `Decision audit log` ในหน้า Inbox

### Service API

```python
list_recent_decisions(
    *,
    limit: int = 50,
    since: datetime | str | None = None,
    kind: str | None = None,
    symbol: str | None = None,
    decision: str | None = None,
    proposal_id: str | None = None,
) -> list[dict]
```

- join `strategy_proposal_decisions` กับ `strategy_proposals` ใน SQL เดียว
- ทุก filter optional
- sort newest first
- ผลลัพธ์รวม symbol, kind, tier, status ของ proposal ที่เกี่ยวข้องทำให้ UI render ได้ทันที

### UI

filter controls 4 ช่อง:
- `Kind` — `all` / `RULE_UPDATE` / `PRUNE`
- `Decision` — `all` / `applied` / `dismissed` / `expired` / `superseded` / `created`
- `Symbol contains` — text input (uppercased)
- `Limit` — number input 5–500

ด้านล่างแสดง caption row ต่อ decision พร้อม reason และ actor

## 6. Ops runbook

### Sweep

- Streamlit app ทำ sweep อัตโนมัติทุก rerun แต่ throttle 60 วินาที (per-session)
- สำหรับ deployment ที่ Streamlit อาจไม่ถูกเปิดบ่อย ให้ใส่ cron:

```cron
*/5 * * * * cd /path/to/Bitkub_Bot && .venv/bin/python scripts/sweep_proposals.py
```

### Suppression / dismissal

- dismiss row 1 ครั้ง = suppress proposal ที่มี `(symbol, kind, rule_hash)` ตัวเดียวกัน 6 ชั่วโมง
- อยากรี-surface เร็ว → เปลี่ยน rule (คือ `rule_hash` เปลี่ยน) จะสร้าง proposal ใหม่ทันที
- อยาก tune dismissal window → parameter `dismissal_seconds` ของ `mark_dismissed`

### TTL

- `RULE_UPDATE` proposal: TTL 300 วินาที (default) จาก `DEFAULT_PROPOSAL_TTL_SECONDS`
- `PRUNE` proposal: ไม่ตั้ง `expires_at` (prune หมายถึง actionable ต่อเนื่องจนกว่าคนจะตัดสิน) — sweep ข้ามไป
- apply ตอนหมด TTL → LedgerError + auto-flip เป็น expired

## 7. Files touched (quick index)

- [services/db_service.py](../services/db_service.py) — schema + indexes
- [services/strategy_proposal_service.py](../services/strategy_proposal_service.py) — `ProposalKind`, `rule_hash`, `stable_proposal_id`, `proposal_id` field
- [services/strategy_proposal_ledger.py](../services/strategy_proposal_ledger.py) — lifecycle API, `run_startup_sweep`
- [services/strategy_proposal_metrics.py](../services/strategy_proposal_metrics.py) — summary + audit log queries
- [ui/streamlit/strategy_inbox.py](../ui/streamlit/strategy_inbox.py) — ledger-backed render, apply/dismiss actions, metrics + audit panels
- [ui/streamlit/app.py](../ui/streamlit/app.py) — startup sweep hook
- [scripts/sweep_proposals.py](../scripts/sweep_proposals.py) — CLI sweep runner
- [tests/test_strategy_proposal_ledger.py](../tests/test_strategy_proposal_ledger.py) — lifecycle, dedup, TTL, suppression, sweep
- [tests/test_strategy_inbox.py](../tests/test_strategy_inbox.py) — persistence, reload, apply, dismiss, suppression
- [tests/test_strategy_proposal_metrics.py](../tests/test_strategy_proposal_metrics.py) — summary + audit filters
