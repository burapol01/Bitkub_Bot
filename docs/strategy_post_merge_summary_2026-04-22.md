# Strategy Post-Merge Summary (PR #56-#60)

สรุปงานฝั่ง Strategy ที่ merge เข้า `main` แล้ว ณ commit `19f3329` หลังเอกสารเดิม [ux_strategy_redesign.md](./ux_strategy_redesign.md) เพื่อให้คนมาอ่านต่อเห็นทั้งภาพรวม rollout, จุดที่เปลี่ยนพฤติกรรมจริง, และไฟล์หลักที่ต้องตามต่อ

ช่วงที่ครอบคลุมในเอกสารนี้:
- PR #56 / commit `86be226` on 2026-04-21
- PR #57 / commit `555c079` on 2026-04-21
- PR #58 / commit `2b5b57b` on 2026-04-21
- PR #59 / commit `08b48ea` on 2026-04-22
- PR #60 / commit `f64c6e0` on 2026-04-22

## Timeline

| PR | Commit | Date | Summary |
| --- | --- | --- | --- |
| #56 | `86be226` | 2026-04-21 | small safe patches for performance freshness and prune readiness |
| #57 | `555c079` | 2026-04-21 | restore linked-order prune actions and align overlay freshness tests |
| #58 | `2b5b57b` | 2026-04-21 | make prune button actually remove from live rules config |
| #59 | `08b48ea` | 2026-04-22 | add proposal service with tier classifier + prune false-block fix |
| #60 | `f64c6e0` | 2026-04-22 | add Strategy Inbox page with tiered proposal workflow |

## 1. Safe Patches Before Inbox Rollout

PR #56 ทำหน้าที่เป็น patch set เล็ก ๆ เพื่อให้หน้า Strategy เดิมพร้อมสำหรับ flow ที่ตามมา โดยมี 3 เรื่องหลัก:

### 1.1 แยก freshness ของ Compare ออกจาก freshness ของ live quote

- Compare section มี badge แยกชัดว่าเป็น `compare data`
- live price overlay เปลี่ยนคำอธิบายเป็น `Live quote overlay (separate from compare data):`
- live quote freshness ถูก render เป็น badge/caption แยกอีกชุดหนึ่ง
- ลดความสับสนว่าข้อมูล replay/candle กับ quote ปัจจุบันเป็น freshness คนละชุด

ผลที่ผู้ใช้เห็น:
- ตอนอยู่หน้า Compare หรือ Live Tuning จะเห็นได้ชัดขึ้นว่าข้อมูลที่ใช้ตัดสินใจมาจาก replay ชุดไหน และ quote ปัจจุบันสดแค่ไหน
- warning เรื่อง stale compare data ไม่ไปปนกับ quote freshness

### 1.2 ทำ operational-state context ให้ reuse ได้

- เพิ่ม `build_symbol_operational_state_context(...)` ใน [ui/streamlit/symbol_state.py](../ui/streamlit/symbol_state.py)
- หน้า Strategy สามารถ reuse execution orders, holdings rows, reconciliation findings ได้จาก context เดียว
- ลดการดึง state ซ้ำ ๆ ระหว่าง Compare / Live Tuning / prune review

ผลที่ได้:
- logic สม่ำเสมอขึ้นระหว่าง panel ต่าง ๆ
- เป็นฐานให้ proposal recompute ใน Inbox reuse operational state เดิมได้ตรง ๆ

### 1.3 แยก hard block vs soft warning สำหรับ prune readiness

- เพิ่ม `_build_prune_symbol_assessment(...)` ใน [ui/streamlit/pages.py](../ui/streamlit/pages.py)
- linked orders / reserved balances ถูกแยกจาก review reasons
- soft warnings เช่น exchange open-orders coverage partial ถูกแยกจาก hard blockers

ผลที่ผู้ใช้เห็น:
- prune review table อธิบายสาเหตุได้ชัดขึ้น
- หน้า UI แสดง linked state, soft warnings, และ hard-block cases เป็นคนละชั้น

## 2. Restore Linked-Order Prune Actions

PR #57 ปรับความหมายของ "linked state" ตอน prune ให้นำไปใช้งานได้จริงมากขึ้น

หลักการใหม่:
- open orders และ reserved balances เป็น "linked state" ที่จัดการได้
- linked state อย่างเดียวไม่ใช่ hard block เสมอไป
- unresolved partial fill และ hard review reasons ยัง block ตามเดิม
- soft exchange-coverage warnings จะถูกยกระดับเป็น hard block ก็ต่อเมื่อยังมี linked state ที่ต้อง cancel จริง

ผลเชิงพฤติกรรม:
- operator สามารถใช้ flow `Cancel linked orders and prune` ได้อีกครั้งในเคสที่ state ชัด
- เคส coverage จาก exchange ไม่ครบ จะไม่ block ทันทีถ้าไม่มี actionable linked order จริง
- logic นี้เป็นรากฐานของ fix "ghost reserved" ใน PR #59

## 3. Make Prune Action Mutate Config For Real

PR #58 แก้ pain point สำคัญของหน้า Strategy เดิม: ปุ่ม prune ต้อง "ลบกฎจริง" แทนที่จะพาผู้ใช้หลุด flow

สิ่งที่เปลี่ยน:
- ตัด radio option `Review in Live Ops` ออกจาก action ภายใน form
- ปุ่ม submit เปลี่ยนจาก `Continue` เป็น `Remove from Live Rules`
- ถ้า symbol อยู่ใน hard-block state จะขึ้น error ชัดเจน และไม่แก้ config
- เพิ่มปุ่มแยก `Open Live Ops to inspect <symbol>` นอก form สำหรับ manual inspection
- อัปเดต [ui_ops_smoke_test_checklist.md](./ui_ops_smoke_test_checklist.md) ให้ตรง flow ใหม่

ผลที่ผู้ใช้เห็น:
- submit ใน prune form มีความหมายชัดว่าแก้ live rules config
- blocked symbols ไม่ถูกพา navigate เงียบ ๆ อีกต่อไป
- การเปิด Live Ops กลายเป็น explicit side action แยกจาก prune mutation

## 4. Proposal Service Backend

PR #59 เพิ่ม backend ใหม่ [services/strategy_proposal_service.py](../services/strategy_proposal_service.py) เพื่อแยก logic proposal ออกจาก Streamlit UI

### สิ่งที่เพิ่ม

- `ProposalTier`
  - `AUTO_APPROVE`
  - `RECOMMENDED`
  - `NEEDS_REVIEW`
  - `BLOCKED`
- dataclass `RuleProposal`
- dataclass `PruneProposal`
- helper สำหรับ group/summarize/build proposal
- unit test ชุดใหญ่ใน [tests/test_strategy_proposal_service.py](../tests/test_strategy_proposal_service.py)

### หลักคิดของ rule-update confidence

confidence สำหรับ update proposal คำนวณจากหลายปัจจัยร่วมกัน:
- edge THB เทียบ baseline
- best PnL
- จำนวน trades
- win rate
- fee guardrail
- freshness ของ compare rows

จากนั้น tier จะถูกจัดให้เป็น:
- `AUTO_APPROVE` เมื่อ signal แข็ง, edge ไม่ติดลบ, ไม่มี warning สำคัญ
- `RECOMMENDED` เมื่อ confidence พอใช้
- `NEEDS_REVIEW` เมื่อ data stale/missing, หลังหัก fee ไม่ดี, หรือ best PnL ยังไม่น่าไว้ใจ
- `BLOCKED` เมื่อมี hard block

### False-block fix: ghost reserved

จุดสำคัญของ PR นี้คือแก้ regression ฝั่ง prune:

- reserved balance ที่ไม่มี open order จริง ไม่ควรถูก block อัตโนมัติ
- service แยก `has_real_open_orders` ออกจาก `has_ghost_reserved`
- ถ้าเป็น ghost reserved จะขึ้น warning ว่า prune ยังทำได้ แต่ควร verify exchange state
- hard block จะเกิดเมื่อมี open order จริง, partial fill ค้าง, หรือ hard review reason เท่านั้น

ผลที่ได้:
- prune queue ไม่ตันเพราะ reserved balance ค้างแบบไม่มี order ให้ cancel
- logic นี้ตรงกับเคส operator ที่เจอ exchange state ไม่สมบูรณ์บางช่วง

## 5. Strategy Inbox Page

PR #60 เพิ่มหน้าใหม่ [ui/streamlit/strategy_inbox.py](../ui/streamlit/strategy_inbox.py) และผูกเข้ากับ app ผ่าน [ui/streamlit/app.py](../ui/streamlit/app.py) กับ [ui/streamlit/refresh.py](../ui/streamlit/refresh.py)

### เป้าหมายของหน้าใหม่

แยก flow แบบ "bot proposes, human approves" ออกจากหน้า Strategy เดิม เพื่อให้:
- operator เห็น proposal รวมทุก live rule ในหน้าเดียว
- update ที่มั่นใจสูงถูก pre-check เป็น batch ได้
- prune candidates ไม่ต้องวิ่ง Compare ซ้ำ
- หน้า Strategy เดิมยังอยู่ต่อระหว่าง rollout

### Flow หลักของ Inbox

1. กด `Refresh prices & recompute proposals`
2. Live Tuning ประเมินทุก live rule
3. symbol ที่ถูกแนะนำ `PRUNE` จะข้าม Compare แล้วไปเข้าคิว prune เลย
4. symbol ที่เหลือจะรัน Compare แล้วสร้าง rule-update proposals
5. ทุก proposal ถูกจัด tier เป็น `AUTO_APPROVE / RECOMMENDED / NEEDS_REVIEW / BLOCKED`
6. operator bulk-apply update หรือ bulk-prune ได้จากหน้าเดียว

### สิ่งที่ผู้ใช้เห็นในหน้า Inbox

- summary cards สำหรับ:
  - auto-approve
  - recommended
  - needs review
  - prune queue
  - skipped
- rule-update proposals แยก bucket ตาม tier
- proposal row แสดง:
  - variant ที่ดีที่สุด
  - confidence
  - compare freshness
  - baseline PnL
  - proposed PnL / edge
  - win rate
  - fee guardrail
  - rule diff แบบ before -> after
- prune queue แยก tier เช่นเดียวกัน
- prune action มี checkbox เดียวสำหรับ `Also remove selected symbols from watchlist`

### Behavior สำคัญ

- update proposal ที่ blocked จะไม่ถูก apply
- prune tier ที่ blocked จะ submit ไม่ได้
- prune action ใน Inbox เขียน config โดยตรง:
  - `Remove rule only`
  - `Remove rule + watchlist`
- snapshot ใน session state จะถูก invalidate หลัง apply เพื่อไม่ให้ข้อเสนอเก่าค้างใช้งานต่อ
- diagnostics ด้านล่างแสดง `Skipped` และ `Ranking notices`

### Integration points

- page ใหม่ชื่อ `Strategy Inbox`
- ถูกเพิ่มใน `PAGE_ORDER`
- อยู่ใน `AUTO_REFRESH_SAFE_PAGES`
- หน้าเดิม `Strategy` ยังไม่ถูกลบ

## Main Files Touched

- [ui/streamlit/pages.py](../ui/streamlit/pages.py)
  - freshness UI
  - prune readiness
  - prune mutation flow บนหน้า Strategy เดิม
- [ui/streamlit/symbol_state.py](../ui/streamlit/symbol_state.py)
  - reusable operational-state context
- [services/strategy_proposal_service.py](../services/strategy_proposal_service.py)
  - proposal backend + tier classifier + ghost-reserved fix
- [ui/streamlit/strategy_inbox.py](../ui/streamlit/strategy_inbox.py)
  - หน้า Inbox ใหม่ทั้งหมด
- [ui/streamlit/app.py](../ui/streamlit/app.py)
  - route/render หน้าใหม่
- [ui/streamlit/refresh.py](../ui/streamlit/refresh.py)
  - page order + auto-refresh allowlist
- [docs/ui_ops_smoke_test_checklist.md](./ui_ops_smoke_test_checklist.md)
  - smoke steps ให้ตรงกับ prune flow ใหม่

## Test Coverage Added Or Updated

- [tests/test_strategy_compare_freshness.py](../tests/test_strategy_compare_freshness.py)
  - compare/live-quote freshness behavior
- [tests/test_streamlit_strategy_page.py](../tests/test_streamlit_strategy_page.py)
  - strategy page regressions สำหรับ prune flow และ overlay wording
- [tests/test_strategy_proposal_service.py](../tests/test_strategy_proposal_service.py)
  - tiering, confidence, prune readiness, ghost reserved
- [tests/test_strategy_inbox.py](../tests/test_strategy_inbox.py)
  - recompute flow, prune skip logic, ghost reserved, blocked prune, freshness mapping

## Operational Notes

- rollout นี้เป็น additive มากกว่า replace
- หน้า `Strategy` เดิมยังเป็นที่ทำงานแบบ manual/deep-dive
- หน้า `Strategy Inbox` เป็น operator queue สำหรับ batch review
- ถ้าจะพัฒนาต่อ ควรถือ `strategy_proposal_service.py` เป็น source of truth สำหรับ tiering และ readiness logic ไม่ให้ UI แตกกฎเอง

## Current State

ณ `main` ตอนนี้:
- linked-order prune flow กลับมาใช้งานได้
- prune button ฝั่งหน้าเดิมแก้ให้ mutate config ชัดแล้ว
- ghost-reserved false block ถูกแก้
- proposal backend พร้อม test
- Strategy Inbox page เปิดใช้งานแล้ว และ coexist กับหน้า Strategy เดิม
