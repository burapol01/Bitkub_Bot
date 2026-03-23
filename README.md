# Bitkub Bot

Bitkub trading workspace with three clear layers:

- `main.py` runs the console engine and auto loop.
- `streamlit_app.py` provides a dashboard and control surface.
- `data/bitkub.db` stores logs, reports, execution history, and diagnostics.

Current direction:

- Console remains the engine.
- Streamlit is a control plane, not the auto runner.
- `config.json` is still the active source of truth.
- SQLite is used for audit, reports, and operational history.

## Current Status

- `paper` mode: usable
- `read-only` mode: usable
- `live` mode: guarded foundation plus manual live controls
- Private API: wallet, balances, open orders, and order history are working
- Streamlit UI: available and improving

What is still intentionally limited:

- Strategy-driven live entry is not wired into the market loop.
- Auto live exit exists as a guarded foundation only.
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
- auto live exit evaluation
- hotkeys
- runtime safety handling

### Streamlit Dashboard

Run the UI with:

```powershell
.venv\Scripts\python.exe -m streamlit run streamlit_app.py
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
- retention settings
- manual live order preset
- rule editor
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
- `live_auto_exit_enabled`
- `live_max_order_thb`
- `live_min_thb_balance`
- `live_slippage_tolerance_percent`
- `live_daily_loss_limit_thb`
- `live_manual_order`
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

### Auto Live Exit

`live_auto_exit_enabled` allows guarded sell evaluation for real holdings.

Current behavior:

- at most one exit order per loop
- only sell side
- requires matching live holding context
- skips symbols with active open orders

Strategy-driven live entry is still disconnected.

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
.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

Check syntax:

```powershell
.venv\Scripts\python.exe -m py_compile main.py streamlit_app.py config.py
```

Check more files:

```powershell
.venv\Scripts\python.exe -m py_compile main.py streamlit_app.py streamlit_ui_pages.py streamlit_ui_data.py streamlit_ui_actions.py streamlit_ui_styles.py streamlit_ui_refresh.py
```

## Important Files

- [main.py](/d:/Project/Bitkub/main.py)
- [streamlit_app.py](/d:/Project/Bitkub/streamlit_app.py)
- [streamlit_ui_pages.py](/d:/Project/Bitkub/streamlit_ui_pages.py)
- [streamlit_ui_data.py](/d:/Project/Bitkub/streamlit_ui_data.py)
- [streamlit_ui_actions.py](/d:/Project/Bitkub/streamlit_ui_actions.py)
- [streamlit_ui_styles.py](/d:/Project/Bitkub/streamlit_ui_styles.py)
- [streamlit_ui_refresh.py](/d:/Project/Bitkub/streamlit_ui_refresh.py)
- [config.py](/d:/Project/Bitkub/config.py)
- [config.json](/d:/Project/Bitkub/config.json)
- [services/db_service.py](/d:/Project/Bitkub/services/db_service.py)
- [services/execution_service.py](/d:/Project/Bitkub/services/execution_service.py)
- [services/reconciliation_service.py](/d:/Project/Bitkub/services/reconciliation_service.py)
- [clients/bitkub_private_client.py](/d:/Project/Bitkub/clients/bitkub_private_client.py)

## Next Likely Steps

- keep polishing Streamlit UX
- add Telegram notifications/control later
- keep console as the engine even if cloud deployment is added later
