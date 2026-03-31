# Bitkub Bot

Bitkub trading workspace with three clear layers:

- `main.py` runs the console engine and auto loop.
- `ui/streamlit/app.py` contains the main dashboard and page routing.
- `streamlit_app.py` is a thin compatibility launcher.
- `data/bitkub.db` stores logs, reports, execution history, and diagnostics.

Current direction:

- Console remains the engine.
- Streamlit is a control plane, not the auto runner.
- `config.json` is still the active source of truth.
- SQLite is used for audit, reports, and operational history.

## Current Status

- `paper` mode: usable
- `read-only` mode: usable
- `live` mode: guarded auto entry, guarded auto exit, and manual live controls
- Private API: wallet, balances, open orders, and order history are working
- Streamlit UI: available and improving

What is still intentionally limited:

- Watchlist symbols are separate from live tradable rules.
- Strategy-driven live entry is wired only when `live_auto_entry_enabled` is on.
- Streamlit does not replace the console engine.

## Architecture

### Console Engine

Run the engine with:

```powershell
.venv\Scripts\python.exe main.py
```

Responsibilities:

- market polling
- paper strategy loop
- live execution foundation
- auto live entry / exit evaluation
- hotkeys
- runtime safety handling

### Streamlit Dashboard

Run the UI with:

```powershell
.venv\Scripts\python.exe -m streamlit run ui/streamlit/app.py
```

If Streamlit is not installed:

```powershell
.venv\Scripts\python.exe -m pip install streamlit
```

Responsibilities:

- edit `config.json`
- inspect market/account/execution state
- run manual live actions
- view reports and diagnostics

Important:

- Saving config in UI writes only to `config.json`.
- The console engine still needs its own reload/apply step.

## VPS Deploy

Recommended target:

- `Ubuntu 24.04 LTS`
- run both `main.py` and `ui/streamlit/app.py` on the same VPS
- keep `config.json`, `runtime_state.json`, and `data/bitkub.db` on that VPS as the single source of truth

Quick start:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
chmod +x scripts/start_engine.sh scripts/start_streamlit.sh
./scripts/start_engine.sh
./scripts/start_streamlit.sh
```

Cloud path overrides:

- `BITKUB_CONFIG_PATH`
- `BITKUB_DB_PATH`
- `BITKUB_RUNTIME_STATE_PATH`
- `BITKUB_APP_ROOT`
- `BITKUB_VENV_PATH`

Full steps and `systemd` templates:

- [deploy/VPS_DEPLOY.md](/d:/Project/Bitkub/deploy/VPS_DEPLOY.md)
- [bitkub-engine.service](/d:/Project/Bitkub/deploy/systemd/bitkub-engine.service)
- [bitkub-streamlit.service](/d:/Project/Bitkub/deploy/systemd/bitkub-streamlit.service)

## Streamlit Pages

### Overview

- trading mode
- daily totals
- market overview table
- control snapshot
- latest execution summary

### Account

- private API capability matrix
- live holdings
- exchange open orders summary

### Live Ops

- manual live order form
- pre-flight validation hints
- guardrail snapshot
- refresh open live orders
- cancel selected live order
- recent execution orders and events

### Reports

- symbol summary
- recent paper trades
- recent execution orders
- recent auto-exit events
- recent runtime errors

### Diagnostics

- SQLite health
- retention summary
- latest account / reconciliation / execution records
- live reconciliation summary
- execution console summary

### Config

- system settings
- watchlist editor
- Telegram foundation settings
- retention settings
- manual live order preset
- live rule editor
- add/remove rules
- applied config change summary after save

## Modes

Defined in [config.json](/d:/Project/Bitkub/config.json):

```json
"mode": "paper"
```

Supported values:

- `paper`: normal paper trading flow
- `read-only`: monitoring only, no paper entries/exits
- `live-disabled`: live execution paths locked
- `live`: guarded live foundation enabled

## Console Hotkeys

- `R` reload config
- `P` manual pause / resume
- `S` show positions
- `D` show daily stats
- `A` account snapshot
- `B` SQLite summary
- `T` reports
- `F` report filter
- `H` health diagnostics
- `O` order probe
- `I` wallet import into paper
- `C` clear local paper positions
- `M` manual live order
- `L` live holdings
- `E` execution view
- `K` cycle selected open execution order
- `U` refresh live order status
- `X` cancel selected live order
- `Q` quit

## Config

Main file: [config.json](/d:/Project/Bitkub/config.json)

Important fields:

- `mode`
- `base_url`
- `fee_rate`
- `interval_seconds`
- `cooldown_seconds`
- `live_execution_enabled`
- `live_auto_entry_enabled`
- `live_auto_exit_enabled`
- `live_max_order_thb`
- `live_min_thb_balance`
- `live_slippage_tolerance_percent`
- `live_daily_loss_limit_thb`
- `live_manual_order`
- `watchlist_symbols`
- `telegram_enabled` / `telegram_control_enabled` / `telegram_notify_events`
- retention fields
- `rules`

Example:

```json
{
  "mode": "paper",
  "base_url": "https://api.bitkub.com",
  "fee_rate": 0.0025,
  "interval_seconds": 10,
  "cooldown_seconds": 60,
  "live_execution_enabled": false,
  "live_auto_entry_enabled": false,
  "live_auto_exit_enabled": false,
  "live_max_order_thb": 500,
  "live_min_thb_balance": 100,
  "live_slippage_tolerance_percent": 1.0,
  "live_daily_loss_limit_thb": 1000,
  "live_manual_order": {
    "enabled": false,
    "symbol": "THB_KUB",
    "side": "buy",
    "order_type": "limit",
    "amount_thb": 100,
    "amount_coin": 0.0001,
    "rate": 29.30
  },
  "watchlist_symbols": ["THB_KUB", "THB_BCH", "THB_FET"],
  "telegram_enabled": false,
  "telegram_control_enabled": false,
  "telegram_notify_events": [
    "safety_pause",
    "manual_live_order",
    "auto_live_entry",
    "auto_live_exit",
    "runtime_error"
  ],
  "market_snapshot_retention_days": 30,
  "signal_log_retention_days": 30,
  "runtime_event_retention_days": 30,
  "account_snapshot_retention_days": 30,
  "reconciliation_retention_days": 30,
  "signal_log_file": "signal_log.csv",
  "trade_log_file": "paper_trade_log.csv",
  "rules": {
    "THB_KUB": {
      "buy_below": 29.12,
      "sell_above": 30.00,
      "budget_thb": 100,
      "stop_loss_percent": 1.0,
      "take_profit_percent": 1.2,
      "max_trades_per_day": 3
    }
  }
}
```

## Live Execution Notes

### Manual Live Order

The `M` hotkey and the `Live Ops` page use `live_manual_order`.

Before using manual live order:

- set `mode = "live"`
- set `live_execution_enabled = true`
- set `live_manual_order.enabled = true`
- confirm symbol, side, size, and rate carefully

### Auto Live Entry

`live_auto_entry_enabled` allows guarded buy evaluation for symbols already present in `rules`.

Current behavior:

- at most one auto entry per loop
- only uses the live tradable shortlist in `rules`
- requires a fresh BUY-zone transition in the market loop
- skips symbols with holdings, open execution orders, or exchange open orders

### Auto Live Exit

`live_auto_exit_enabled` allows guarded sell evaluation for real holdings.

Current behavior:

- at most one exit order per loop
- only sell side
- requires matching live holding context
- skips symbols with active open orders

### Watchlist vs Rules

- `watchlist_symbols` = research universe for candle sync, ranking, and replay
- `rules` = live tradable shortlist used by the console engine

## Private API

Put credentials in [.env](/d:/Project/Bitkub/.env):

```env
BITKUB_API_KEY=your_key
BITKUB_API_SECRET=your_secret
```

Current private coverage:

- wallet
- balances
- open orders
- order history
- manual live order actions

## Data Files

### Runtime

- [runtime_state.json](/d:/Project/Bitkub/runtime_state.json)

Stores:

- manual pause state
- paper positions
- cooldowns
- daily stats
- last zones

### CSV

- [signal_log.csv](/d:/Project/Bitkub/signal_log.csv)
- [paper_trade_log.csv](/d:/Project/Bitkub/paper_trade_log.csv)

### SQLite

- [bitkub.db](/d:/Project/Bitkub/data/bitkub.db)

Main tables:

- `runtime_events`
- `signal_logs`
- `market_snapshots`
- `paper_trade_logs`
- `account_snapshots`
- `reconciliation_results`
- `execution_orders`
- `execution_order_events`

## Retention

Automatic cleanup currently applies to:

- `market_snapshots`
- `signal_logs`
- `runtime_events`
- `account_snapshots`
- `reconciliation_results`

Current cleanup triggers:

- startup
- successful config reload
- at most once per day during runtime

`paper_trade_logs` are not auto-pruned yet.

## Safety Behavior

The engine can enter `safety pause` for important mismatches or invalid state, such as:

- invalid `config.json`
- config/rule mismatch against current runtime state
- reconciliation mismatch in guarded modes

When this happens:

- execution stops
- diagnostics remain available
- fix the issue, then reload

## Useful Commands

Run console engine:

```powershell
.venv\Scripts\python.exe main.py
```

Run Streamlit UI:

```powershell
.venv\Scripts\python.exe -m streamlit run ui/streamlit/app.py
```

Check syntax:

```powershell
.venv\Scripts\python.exe -m py_compile main.py streamlit_app.py ui\streamlit\app.py config.py
```

Check more files:

```powershell
.venv\Scripts\python.exe -m py_compile main.py streamlit_app.py ui\streamlit\app.py ui\streamlit\pages.py ui\streamlit\data.py ui\streamlit\actions.py ui\streamlit\styles.py ui\streamlit\refresh.py
```

## Important Files

- [main.py](/d:/Project/Bitkub/main.py)
- [streamlit_app.py](/d:/Project/Bitkub/streamlit_app.py)
- [app.py](/d:/Project/Bitkub/ui/streamlit/app.py)
- [pages.py](/d:/Project/Bitkub/ui/streamlit/pages.py)
- [data.py](/d:/Project/Bitkub/ui/streamlit/data.py)
- [actions.py](/d:/Project/Bitkub/ui/streamlit/actions.py)
- [styles.py](/d:/Project/Bitkub/ui/streamlit/styles.py)
- [refresh.py](/d:/Project/Bitkub/ui/streamlit/refresh.py)
- [config.py](/d:/Project/Bitkub/config.py)
- [config.json](/d:/Project/Bitkub/config.json)
- [services/db_service.py](/d:/Project/Bitkub/services/db_service.py)
- [services/execution_service.py](/d:/Project/Bitkub/services/execution_service.py)
- [services/reconciliation_service.py](/d:/Project/Bitkub/services/reconciliation_service.py)
- [clients/bitkub_private_client.py](/d:/Project/Bitkub/clients/bitkub_private_client.py)

## Next Likely Steps

- keep polishing Streamlit UX
- add richer Telegram control and confirmation flows
- keep console as the engine even if cloud deployment is added later

## Telegram Foundation

The codebase now includes a Telegram notifications-and-control foundation:

- critical events are queued into SQLite `telegram_outbox`
- the console engine flushes queued notifications to Telegram when delivery settings are ready
- Telegram commands are polled from `getUpdates` and logged into `telegram_command_log`
- enabling Telegram in config does not start a webhook server or separate bot process by itself

Supported Telegram commands now include:

- `/start`
- `/help`
- `/help config`
- `/status`
- `/health`
- `/positions`
- `/holdings`
- `/orders`
- `/latest`
- `/live`
- `/config`
- `/buy <symbol> <amount_thb> <rate>`
- `/sell <symbol> <amount_coin> <rate>`
- `/set_config <field> <value>`
- `/set_config_fields`
- `/set_rule <symbol> <buy_below> <sell_above> <budget_thb> <stop_loss_percent> <take_profit_percent> <max_trades_per_day>`
- `/promote_symbol <symbol>`
- `/pause`
- `/resume`
- `/cancel <execution_order_id>`
- `/reload`
- `/confirm <code>`

Dangerous commands use a confirmation code flow. The bot replies with `/confirm <code>` instructions before it applies `/buy`, `/sell`, `/set_config`, `/set_rule`, `/promote_symbol`, `/pause`, `/resume`, `/cancel`, or `/reload`.

Required `.env` values for delivery:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_IDS=123456789,987654321
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

You can use `TELEGRAM_CHAT_ID` instead when only one chat should receive notifications. If `TELEGRAM_ALLOWED_CHAT_IDS` is omitted, command authorization falls back to the notify chat ids.

### Find `TELEGRAM_CHAT_ID` with `telegram_test_token.py`

Use the helper script below to confirm which chat id is currently resolved from your env file and to ask Telegram for the latest chats seen by the bot.

1. Open Telegram and send a message such as `/start` to your bot first.
2. Run the helper script from the project root.

```bash
python telegram_test_token.py --env-file env_dev
```

What the script does:

- loads the selected env file, such as `env_dev`
- reads `TELEGRAM_CHAT_ID`, `TELEGRAM_CHAT_IDS`, and `TELEGRAM_ALLOWED_CHAT_IDS` using the same fallback logic as the app
- calls Telegram `getUpdates` so you can see which `chat_id` should be used

Observed output from the current workspace run:

```text
Loaded env file: env_dev
TELEGRAM_BOT_TOKEN present: True
Notify chat ids: ['12345567890']
Control chat ids: ['12345567890']

Use TELEGRAM_CHAT_ID when there is only one destination chat.
Use TELEGRAM_CHAT_IDS=id1,id2 when notifications should go to multiple chats.
Private chat ids are usually positive numbers; group or supergroup ids are usually negative.

Telegram API lookup failed: requests is not installed. Install project dependencies before using Telegram API lookup.
```

If you see the same `requests is not installed` message, install the project dependencies in the Python environment you are using and run the command again. If you only want to verify env parsing without calling Telegram, use:

```bash
python telegram_test_token.py --env-file env_dev --skip-api
```
