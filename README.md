# Bitkub Paper Trade Tracker

บอทตัวนี้เป็นระบบ `paper trading` สำหรับ Bitkub ที่รันใน console บน Windows โดยเน้น:
- อ่านราคาจาก Bitkub public API
- ประเมิน zone `BUY / WAIT / SELL`
- จำลองการซื้อขายแบบ paper trade
- reload `config.json` แบบ manual
- เก็บ log ทั้ง CSV และ SQLite
- อ่านข้อมูลบัญชีแบบ `private read-only`

สถานะปัจจุบัน:
- `paper` mode ใช้งานได้
- `read-only` mode ใช้งานได้
- `live-disabled` มีไว้ล็อก execution และยังไม่ใช่ real trading

## การรัน

ใช้ virtual environment แล้วรัน:

```powershell
.venv\Scripts\python.exe main.py
```

## Live Auto Exit

- Set `"mode": "live"` and `"live_execution_enabled": true`
- Set `"live_auto_exit_enabled": true` to allow guarded auto-sell from real exchange holdings
- Auto exit currently submits at most one sell order per loop
- It only evaluates holdings that:
  - have available balance on the exchange
  - have no open exchange/live execution order for the same symbol
  - have a latest filled live execution record with side=`buy`
- Exit triggers currently use the configured symbol rule:
  - `STOP_LOSS`
  - `TAKE_PROFIT`
  - `SELL_ZONE`
- Strategy-driven live entry is still disconnected in this build

## โหมดการทำงาน

กำหนดใน [config.json](/d:/Project/Bitkub/config.json)

```json
"mode": "paper"
```

ค่าที่รองรับ:
- `paper`:
  ดูตลาด, log signal, เปิด/ปิด paper position ได้
- `read-only`:
  ดูตลาด, log signal, อ่าน private API ได้ แต่จะไม่เปิด/ปิด paper position
- `live`:
  เปิด foundation สำหรับ live trading, execution guardrails, และ order diagnostics แต่ build ปัจจุบันยังไม่เชื่อม strategy loop ไปสู่การส่ง order จริงอัตโนมัติ
- `live-disabled`:
  ล็อก execution ไว้ชัดเจนสำหรับ build นี้ ใช้ดูสถานะและทดสอบ flow เท่านั้น

## Hotkeys

ระหว่างโปรแกรมรันใน console:

- `R` = reload `config.json`
- `P` = manual pause / resume
- `S` = แสดง open positions
- `D` = แสดง daily stats
- `A` = แสดง account snapshot จาก private API
- `B` = แสดง database summary จาก SQLite
- `T` = แสดง reports จาก SQLite
- `F` = สลับ report filter ระหว่าง `ALL` และแต่ละ symbol
- `H` = แสดง health diagnostics ของระบบ
- `O` = แสดง order foundation probe
- `I` = import เหรียญที่มี `available balance` ใน wallet มาเป็น local paper positions
- `C` = clear local paper positions ใน runtime state
- `M` = submit manual live order ตาม preset ใน config
- `L` = แสดง live holdings จาก exchange balances
- `Q` = quit แบบ graceful

หมายเหตุ:
- ถ้าเป็น `safety pause` ปุ่ม `P` จะไม่ปลด pause
- ต้องแก้ปัญหาแล้วกด `R` เพื่อ clear safety pause

## Config หลัก

ไฟล์: [config.json](/d:/Project/Bitkub/config.json)

ตัวอย่างโครงสร้าง:

```json
{
  "mode": "paper",
  "base_url": "https://api.bitkub.com",
  "fee_rate": 0.0025,
  "interval_seconds": 10,
  "cooldown_seconds": 60,
  "live_execution_enabled": false,
  "live_max_order_thb": 500,
  "live_min_thb_balance": 100,
  "live_slippage_tolerance_percent": 1.0,
  "live_daily_loss_limit_thb": 1000,
  "live_manual_order": {
    "enabled": false,
    "symbol": "THB_BTC",
    "side": "buy",
    "order_type": "limit",
    "amount_thb": 100,
    "amount_coin": 0.0001,
    "rate": 1
  },
  "market_snapshot_retention_days": 30,
  "signal_log_retention_days": 30,
  "runtime_event_retention_days": 30,
  "account_snapshot_retention_days": 30,
  "reconciliation_retention_days": 30,
  "signal_log_file": "signal_log.csv",
  "trade_log_file": "paper_trade_log.csv",
  "rules": {
    "THB_KUB": {
      "buy_below": 29.50,
      "sell_above": 29.71,
      "budget_thb": 100,
      "stop_loss_percent": 1.0,
      "take_profit_percent": 1.2,
      "max_trades_per_day": 3
    }
  }
}
```

ความหมายคร่าว ๆ:
- `mode`: โหมดการทำงานของระบบ
- `fee_rate`: ค่าธรรมเนียมต่อฝั่ง เช่น `0.0025` = `0.25%`
- `interval_seconds`: ความถี่ของ loop
- `cooldown_seconds`: เวลาพักหลังปิด position
- `live_execution_enabled`: kill switch สำหรับเส้นทาง live execution
- `live_max_order_thb`: วงเงินสูงสุดต่อคำสั่งที่ยอมให้ live path ใช้
- `live_min_thb_balance`: THB ขั้นต่ำที่ต้องมีใน wallet ก่อน live path จะถือว่าพร้อม
- `live_slippage_tolerance_percent`: slippage tolerance สำหรับ live path
- `live_daily_loss_limit_thb`: ขีดจำกัดขาดทุนรายวันสำหรับ live path
- `live_manual_order`: preset สำหรับ hotkey `M`
- `market_snapshot_retention_days`: จำนวนวันที่เก็บ `market_snapshots` ไว้ใน SQLite
- `signal_log_retention_days`: จำนวนวันที่เก็บ `signal_logs` ไว้ใน SQLite
- `runtime_event_retention_days`: จำนวนวันที่เก็บ `runtime_events` ไว้ใน SQLite
- `account_snapshot_retention_days`: จำนวนวันที่เก็บ `account_snapshots` ไว้ใน SQLite
- `reconciliation_retention_days`: จำนวนวันที่เก็บ `reconciliation_results` ไว้ใน SQLite
- `rules`: กติกาต่อ symbol

## Private API

ถ้าต้องการใช้ hotkey `A` และ account snapshot ให้ใส่ค่าใน [.env](/d:/Project/Bitkub/.env)

```env
BITKUB_API_KEY=your_key
BITKUB_API_SECRET=your_secret
```

สถานะปัจจุบันของระบบ:
- รองรับ `wallet` และ `balances` แบบ read-only
- `open_orders` อาจใช้ไม่ได้ ขึ้นอยู่กับ permission ของ API key

ถ้า header ขึ้นประมาณนี้ ถือว่าปกติสำหรับคีย์ read-only บางชุด:

```text
Private API: wallet/balance ready, some order endpoints unavailable
Capabilities: wallet=OK | balances=OK | open_orders=UNAVAILABLE
```

## ข้อมูลที่ระบบเก็บ

### CSV

- [signal_log.csv](/d:/Project/Bitkub/signal_log.csv)
- [paper_trade_log.csv](/d:/Project/Bitkub/paper_trade_log.csv)

### Runtime state

- [runtime_state.json](/d:/Project/Bitkub/runtime_state.json)

ใช้เก็บ:
- last zones
- open paper positions
- daily stats
- cooldowns
- manual pause state

### SQLite

ฐานข้อมูลอยู่ที่ [bitkub.db](/d:/Project/Bitkub/data/bitkub.db)

ตารางหลัก:
- `runtime_events`
- `signal_logs`
- `market_snapshots`
- `paper_trade_logs`
- `account_snapshots`
- `reconciliation_results`

hotkey `B` ใช้อ่าน summary จากฐานข้อมูลนี้
และดู analytics ของ market snapshots ราย symbol สำหรับวันปัจจุบันได้

hotkey `T` ใช้ดู report ที่เน้น:
- summary ราย symbol
- paper trade history ล่าสุด
- runtime errors ล่าสุด

hotkey `F` ใช้สลับ filter ของ report:
- `ALL`
- `THB_BTC`
- `THB_ETH`
- `THB_KUB`
- และ symbol อื่นที่มีใน `rules`

ลำดับการใช้งาน:
- กด `F` เพื่อเลือก filter ที่ต้องการ
- กด `T` เพื่อเปิด report ตาม filter ปัจจุบัน
- ถ้าไม่กด `F` เลย `T` จะใช้ `ALL`

hotkey `H` ใช้ดู health diagnostics เช่น:
- runtime state
- trading mode
- execution enabled / disabled
- live execution guardrails และ blocked reasons
- path ของ config / state / SQLite
- SQLite size และ row counts
- retention policy ของ SQLite
- private API status
- order foundation status
- latest account snapshot
- latest reconciliation
- market snapshot ล่าสุด

hotkey `O` ใช้ดู order foundation probe:
- ตรวจสถานะ order-capable foundation
- ตรวจ `open_orders` และ `order_history` แบบ read-only ต่อ symbol
- ตรวจ endpoint variants หลายรูปแบบเพื่อช่วย debug permission/request issues
- แสดงทั้ง input payload และ wire payload ของ `place_bid`, `place_ask`, `cancel_order`
- ไม่ส่งคำสั่งซื้อขายจริง

hotkey `I` ใช้ import wallet balances เข้า paper:
- ใช้ได้เฉพาะตอน `mode=paper`
- จะอ่าน `available balance` จาก private API
- จะ import เฉพาะ asset ที่มี symbol อยู่ใน `rules` เช่น `BTC -> THB_BTC`
- จะใช้ราคา ticker ล่าสุดเป็นราคา entry ของ local paper position
- จะไม่ส่ง order จริงไปที่ Bitkub
- ถ้ามี paper position ของ symbol นั้นอยู่แล้ว ระบบจะข้าม symbol นั้น

hotkey `C` ใช้ clear local paper positions:
- ล้างเฉพาะ local paper positions ใน `runtime_state.json`
- ไม่แตะยอดจริงใน wallet และไม่ส่ง order จริง
- เหมาะสำหรับล้าง state เก่าที่ทำให้ reconciliation mismatch
- ถ้าอยู่ใน `safety pause` จาก mismatch ให้กด `C` แล้วตามด้วย `R`

hotkey `M` ใช้ submit manual live order:
- ใช้ได้เมื่อ `mode=live`
- ต้องเปิด `live_execution_enabled=true`
- ต้องเปิด `live_manual_order.enabled=true`
- จะอ่าน preset จาก `live_manual_order`
- จะเช็ก guardrails ก่อนส่งจริง
- จะบันทึก `execution_orders` และ `execution_order_events` ลง SQLite
- หลังส่งแล้วจะ refresh `order_info` ทันทีเพื่ออัปเดต state machine

hotkey `L` ใช้ดู live holdings:
- refresh account snapshot จาก exchange
- แสดง `available` / `reserved` / มูลค่า THB ตามราคา ticker ล่าสุด
- แสดง last filled execution order ที่อ้างอิงได้จาก SQLite

SQLite retention cleanup:
- ระบบจะลบข้อมูลเก่าอัตโนมัติจาก:
  - `market_snapshots`
  - `signal_logs`
  - `runtime_events`
  - `account_snapshots`
  - `reconciliation_results`
- `paper_trade_logs` ยังไม่ถูกลบอัตโนมัติในตอนนี้
- cleanup จะรันตอน startup
- cleanup จะรันอีกครั้งหลัง reload config สำเร็จ
- ระหว่าง runtime จะรันไม่เกินวันละครั้ง

## Safety behavior

ระบบจะเข้า `safety pause` ในกรณีสำคัญ เช่น:
- `config.json` invalid ตอนกด `R`
- reload แล้ว symbol ถูกลบ แต่ยังมี open position ค้างอยู่
- startup reconciliation ไม่ตรงกับ balances จริง

เมื่อเกิดขึ้น:
- bot จะหยุด execution
- ยังดูสถานะผ่าน hotkeys ได้
- ต้องแก้ปัญหาแล้วกด `R`

## Troubleshooting

### กด `R` แล้วเข้า safety pause

สาเหตุที่พบบ่อย:
- `config.json` syntax ผิด
- ชนิดข้อมูลผิด เช่นใส่ตัวอักษรในช่องตัวเลข
- ลบ symbol ออกจาก config แต่ยังมี open position ค้างอยู่

แนวทางแก้:
- ตรวจ [config.json](/d:/Project/Bitkub/config.json)
- แก้ไฟล์ให้ถูก
- กด `R` อีกครั้ง

### กด `A` แล้วขึ้นว่า order endpoints unavailable

ตัวอย่าง:

```text
Private API: wallet/balance ready, some order endpoints unavailable
Capabilities: wallet=OK | balances=OK | open_orders=UNAVAILABLE
```

ความหมาย:
- API key ใช้งาน `wallet` และ `balances` ได้
- แต่ permission ปัจจุบันยังไม่พอสำหรับ `open_orders`

แนวทางแก้:
- ตรวจ permission ของ API key ใน Bitkub
- ถ้าอยากใช้แค่ read-only snapshot ตอนนี้ ถือว่าใช้งานต่อได้

### เปลี่ยน `mode` แล้วบอทไม่เปิด paper trade

ให้ตรวจค่า `mode` ใน [config.json](/d:/Project/Bitkub/config.json)

- `paper`:
  เปิด/ปิด paper position ได้
- `read-only`:
  ดูตลาดและ log signal ได้ แต่ execution จะถูกล็อก
- `live`:
  เปิด live foundation และ guardrails ได้ แต่ market loop ยังไม่ส่ง real order อัตโนมัติใน build ปัจจุบัน
- `live-disabled`:
  execution ถูกล็อกโดยตั้งใจใน build นี้

### อยากดูสภาพระบบเร็ว ๆ

กด `H`

เหมาะสำหรับตรวจ:
- ตอนนี้ระบบ paused หรือไม่
- mode อะไรอยู่
- execution เปิดอยู่ไหม
- SQLite / runtime state path ถูกไหม
- private API พร้อมแค่ไหน

## Flow แนะนำในการใช้งาน

1. ตั้ง `mode` เป็น `paper`
2. รันบอท
3. ดูหน้า dashboard หลัก
4. กด `A` เพื่อตรวจ private API snapshot
5. กด `B` เพื่อตรวจว่า SQLite เก็บข้อมูลได้
6. ปรับ `config.json` แล้วกด `R`
7. ถ้าต้องการดูอย่างเดียวโดยไม่ให้เกิด paper execution ให้ใช้ `read-only`

## Execution guardrails

ระบบจะล็อก execution ตาม mode:
- `paper`: execution เปิด
- `read-only`: ปิด paper entries/exits แต่ยังดูตลาดและ log signal ได้
- `live`: ใช้ health/guardrail/order foundation ได้ แต่ strategy-driven execution ยังไม่ถูก wire เข้าตลาด
- `M`: ใช้ manual live execution preset เพื่อทดสอบ order จริงแบบควบคุมได้
- `live-disabled`: ปิด execution ทั้งหมดใน build นี้

เมื่อ mode ล็อก execution:
- dashboard จะขึ้น notice ชัดเจน
- trade engine จะไม่ถูกเรียก
- จะมี runtime event ถูกบันทึกลง SQLite

## ไฟล์สำคัญ

- [main.py](/d:/Project/Bitkub/main.py): orchestration loop และ hotkeys
- [config.py](/d:/Project/Bitkub/config.py): config load / validate / reload
- [clients/bitkub_client.py](/d:/Project/Bitkub/clients/bitkub_client.py): public ticker
- [clients/bitkub_private_client.py](/d:/Project/Bitkub/clients/bitkub_private_client.py): private read-only API
- [core/trade_engine.py](/d:/Project/Bitkub/core/trade_engine.py): paper trade engine
- [services/ui_service.py](/d:/Project/Bitkub/services/ui_service.py): console UI
- [services/db_service.py](/d:/Project/Bitkub/services/db_service.py): SQLite
- [services/state_service.py](/d:/Project/Bitkub/services/state_service.py): runtime persistence
- [services/reconciliation_service.py](/d:/Project/Bitkub/services/reconciliation_service.py): startup reconciliation

## ข้อจำกัดตอนนี้

- ยังไม่ใช่ real trading bot
- ยังไม่มี strategy-driven live execution จริงใน market loop
- มี private order-capable foundation, execution service, และ order state machine foundation สำหรับ `place-bid`, `place-ask`, `cancel-order`
- มี manual live execution path ผ่าน hotkey `M` พร้อม guardrails และ state logging
- `live-disabled` เป็นโหมดล็อก execution ไม่ใช่ live mode จริง
- ยังต้องเพิ่ม reconciliation เต็มรูปแบบจาก `open_orders / order_info` และ risk guardrails ก่อนทดสอบเงินจริง

## คำสั่งที่ใช้บ่อย

รันโปรแกรม:

```powershell
.venv\Scripts\python.exe main.py
```

เช็ก syntax:

```powershell
.venv\Scripts\python.exe -m py_compile main.py
```

เช็กหลายไฟล์:

```powershell
.venv\Scripts\python.exe -m py_compile main.py config.py services\db_service.py services\ui_service.py
```

## Streamlit UI

Run the dashboard:

```powershell
streamlit run streamlit_app.py
```

Install Streamlit in the venv if needed:

```powershell
.venv\Scripts\python.exe -m pip install streamlit
```
