# Runtime Reconciliation

This bot keeps two kinds of state that can drift apart:

- local runtime state in `runtime_state.json` plus SQLite `execution_orders`, `execution_order_events`, and trade history
- exchange-visible state from balances, open orders, and order lookups

## What Runs

Structured reconciliation now runs in two places:

- startup
- periodic background pass every 5 minutes while the engine is in `live` or `shadow-live`

Each run is stored in SQLite `state_reconciliation_runs`. A lighter summary is also written to `reconciliation_results` for compatibility with existing diagnostics.

## Mismatch Categories

- `missing_locally`: exchange open order exists but there is no local `execution_orders` row
- `missing_on_exchange`: local open execution order is not present in the latest exchange open-orders snapshot
- `orders_without_exchange_id`: local open execution order has no recorded exchange id
- `stale_pending`: local open execution order has stayed non-terminal too long
- `partially_filled`: local order is still in a partial-fill state
- `reserved_without_open_order`: exchange reserved balance exists but no open order is visible
- `open_order_without_reserved`: exchange open order exists but reserved balance is zero
- `unmanaged_live_holdings`: exchange holding exists without a tracked filled buy execution record
- `runtime_state_stale`: runtime state was restored from `runtime_state.pending.json`, has an invalid `saved_at`, or the saved snapshot is too old

## Safe Correction Rules

Reconciliation is conservative.

It may auto-correct only when exchange truth is clear for an already tracked local open execution order:

- refresh the tracked order with `refresh_live_order_from_exchange`
- persist the new state, timestamps, exchange ids, and execution order events

It does not:

- create new execution rows for exchange-only open orders
- delete local evidence because the exchange snapshot is empty or unavailable
- place new orders as part of reconciliation

## Unresolved / Review-Only Cases

The following are recorded and surfaced for review instead of guessed:

- exchange API unavailable or partially available
- exchange open order exists without a local record
- local record missing an exchange id
- holdings imply a mismatch that cannot be attributed safely
- runtime state freshness problems

## Storage

- `state_reconciliation_runs`: structured reconciliation history, mismatch counts/details, safe corrections, and notes
- `reconciliation_results`: compact compatibility summary
- `runtime_events`: operator-facing event log when a run needs review or makes a safe correction

## Diagnostics

The Streamlit `Diagnostics` page shows:

- latest structured reconciliation run
- account sync status
- runtime state status
- unresolved count
- stale pending count
- corrected order count
- recent reconciliation run table
- mismatch details in an expander
