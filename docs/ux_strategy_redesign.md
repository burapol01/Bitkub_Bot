# UX Strategy Redesign — Phase 1–8 + Section Intro Consistency

สรุปงาน UX redesign ของหน้า Strategy (และงานตาม) ที่ merge เข้า `main` แล้ว ใช้เป็นบันทึกอ้างอิงสำหรับคนอ่าน diff / คนเข้ามาทำต่อ

## ไทม์ไลน์ commit (main)

| PR  | Commit    | หัวข้อ                                       |
| --- | --------- | -------------------------------------------- |
| #44 | `4784e4d` | Phase 1 — rename workspaces (Compare / Live Tuning) |
| #46 | `e186123` | Phase 2 — add Decisions workspace            |
| #47 | `cfacaff` | Phase 3 — decision context handoff + polish  |
| #49 | `e3c6438` | Phase 4 — unify Decisions handoff callouts   |
| #50 | `45774d5` | Phase 5 — Sync & Rank handoff from Decisions |
| #51 | `e27b7b7` | Phase 6 — reviewed-this-session ledger       |
| #52 | `5f83c95` | Phase 7 — unify workspace section intros     |
| #53 | `ae77fb8` | Phase 8 — real outcome tracking in ledger    |
| #54 | `16d4d7e` | Section intro consistency (Config / Diagnostics) |

## สิ่งที่เปลี่ยน ต่อ phase

### Phase 1 — Workspace rename
- เปลี่ยนชื่อ workspace "Compare Lab" → **Compare** และ "Live Rules" → **Live Tuning**
- ใช้ชื่อสั้นลง สื่อหน้าที่ตรงกว่า (เทียบ vs. ปรับจริง)

### Phase 2 — Decisions workspace
- เพิ่ม workspace ใหม่ **Decisions** เป็น default landing
- รวม Decision Queue + summary ให้อยู่หน้าเดียว ไม่ต้องสลับไปมา

### Phase 3 — Decision context handoff
- เพิ่ม `strategy_decision_context` ใน `st.session_state` สำหรับส่งข้อความ context ข้าม workspace
- Polish Decisions workspace (kicker, section intro, ordering)

### Phase 4 — Unify Decisions handoff callouts
- ใน **Live Tuning**: ย้าย "From Decisions" callout ไปไว้บนสุดของ workspace (เหนือ panel-title)
- ใน **Compare**: รวม "From Decisions" + "Pre-filled from Live Rules" เป็น callout เดียว ใช้ `<br><br>` แยกย่อหน้า
- ลบ pattern consumed-flag (`_decision_context = None`) เปลี่ยนเป็น `elif` flat

### Phase 5 — Sync & Rank handoff symmetry
- ปุ่ม "Sync These Now" ใน Decision Queue → set `strategy_decision_context` ก่อน navigate
- หัว **Sync & Rank** workspace: pop + render "From Decisions" callout เหมือน Compare / Live Tuning

### Phase 6 — Reviewed-this-session ledger
- helper `_record_decision_review(symbol, action)` — session-scoped ordered dict
- Wire 4 ปุ่ม Open ใน Decision Queue ให้บันทึก
- แสดง ledger บนสุดของ Decisions workspace + ปุ่ม Clear (layout `[0.85, 0.15]`)

### Phase 7 — Workspace section intro consistency
- **Overview** และ **Sync & Rank** เดิมใช้ `<div class="panel-title">` + caption
- เปลี่ยนเป็น `render_section_intro(title, description, kicker)` ให้ตรงกับ Compare / Live Tuning / Decisions

### Phase 8 — Real outcome tracking
- `_record_decision_review` เก็บเป็น dict: `{"action", "was_in_rules"}` แทน string
- helper `_decision_review_status(entry, sym, current_rule_symbols)` คำนวณผลจริง:
  - `Compare` + not in rules → now in rules → **Done (promoted)**
  - `Live Tuning` + was in rules → not in rules → **Done (pruned)**
- Ledger display แสดง suffix ` · Done (promoted)` / ` · Done (pruned)` อัตโนมัติ

### Section intro consistency (Option C, #54)
- **Config Editor** (`config_support.py`): `panel-title` + caption → `render_section_intro`
- **Diagnostics** (`diagnostics_support.py`): ไม่มี intro → เพิ่ม `render_section_intro` ที่หัวหน้า
- หน้าอื่น (Live Ops, Reports, Execution Assistant, Overview, Account, Logs, Strategy) ใช้ `render_section_intro` อยู่แล้ว ไม่แตะ

## Session state keys ที่เกี่ยวข้อง

| Key                                        | หน้าที่                                                         |
| ------------------------------------------ | --------------------------------------------------------------- |
| `strategy_decision_context`                | ข้อความ handoff ระหว่าง workspace (pop เมื่อ render)            |
| `strategy_decision_reviewed_ledger`        | Ordered dict `{symbol: {action, was_in_rules}}` (session-scope) |
| `strategy_workspace_autorun`               | Target workspace สำหรับ cross-page nav                          |
| `strategy_workspace_focus_symbol`          | Symbol focus เมื่อเปลี่ยน workspace                             |
| `strategy_compare_symbol_autorun`          | Pre-fill symbol ของ Compare                                     |
| `strategy_tuning_focus_symbol_autorun`     | Pre-fill symbol ของ Live Tuning                                 |
| `strategy_queue_sync_symbols`              | Symbols ที่ flag จาก Decisions เข้าไป Sync & Rank               |

Helper สำหรับ queue navigation ดูที่ [ui/streamlit/navigation.py](../ui/streamlit/navigation.py)

## ไฟล์หลักที่แตะ

- [ui/streamlit/pages.py](../ui/streamlit/pages.py) — Strategy page, Phase 1–8 ทั้งหมด
- [ui/streamlit/navigation.py](../ui/streamlit/navigation.py) — `queue_strategy_workspace_navigation`
- [ui/streamlit/config_support.py](../ui/streamlit/config_support.py) — #54
- [ui/streamlit/diagnostics_support.py](../ui/streamlit/diagnostics_support.py) — #54
- [ui/streamlit/styles.py](../ui/streamlit/styles.py) — `render_section_intro`, `render_callout` (รองรับ `unsafe_allow_html`)

## Pattern ที่ใช้ซ้ำ (สำหรับเพิ่ม workspace/หน้าใหม่)

1. **หัว workspace**: `render_section_intro(title, description, kicker)` เท่านั้น อย่ากลับไปใช้ `panel-title`
2. **Cross-workspace handoff**:
   ```python
   _ctx = st.session_state.pop("strategy_decision_context", None)
   if _ctx:
       render_callout("From Decisions", _ctx, "info")
   ```
   วางไว้บนสุดของ workspace ก่อน content อื่น
3. **`render_callout` message** รองรับ HTML — ใช้ `<br><br>` แทน `\n\n` สำหรับย่อหน้า
4. **Ledger-style list** ใน session state: ใช้ `dict` + `pop(sym); ledger[sym] = ...` เพื่อ "most recent last" (Python 3.7+ ordering)

## Tests ที่ครอบคลุม

- `tests.test_streamlit_strategy_page` — workspace rendering, handoff, ledger
- `tests.test_streamlit_config_page` — Config section intro
- `tests.test_streamlit_live_ops_page` — regression ใช้ยืนยัน section intro pattern

## จบงาน

ทุก phase merge เข้า main เรียบร้อย branch `feat/ux-strategy-phase{1..8}-*` และ `feat/ux-pages-section-intro-consistency` ยังคงอยู่ (local) ลบได้ถ้าต้องการทำความสะอาด
