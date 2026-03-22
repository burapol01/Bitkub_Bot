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
- `H` = แสดง health diagnostics ของระบบ
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

hotkey `H` ใช้ดู health diagnostics เช่น:
- runtime state
- trading mode
- execution enabled / disabled
- path ของ config / state / SQLite
- private API status
- latest account snapshot
- latest reconciliation
- market snapshot ล่าสุด

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
- ยังไม่มี order execution จริง
- `live-disabled` เป็นโหมดล็อก execution ไม่ใช่ live mode จริง
- `open_orders` private endpoint อาจใช้ไม่ได้ถ้า permission ของ API key ไม่พอ

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
