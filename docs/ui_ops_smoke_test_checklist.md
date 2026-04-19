# UI/Ops Smoke Test Checklist

Use this after UI or operator-flow changes that touch `Strategy`, `Execution Assistant`, `Live Ops`, or rule editing.

## Setup

- Prefer `paper` mode or a non-production config copy.
- Use a symbol set that gives you:
  - one normal live rule
  - one `PRUNE` candidate with linked orders or reserved balances
  - one symbol that lands in `review required`
  - one recent auto-exit slippage block for the exit helper
- For quote-sensitive checks, confirm the quote is fresh before trusting any suggested safe price.
- Do not submit a real manual order unless you are explicitly doing live-account validation.

## 1. Compare -> Execution Assistant -> Save Adjusted Rule

- In `Strategy -> Compare`, run Compare for a live-rule symbol and note the current `buy_below` / `sell_above`.
- Open `Execution Assistant` for the same symbol.
- Use `Snap Buy`, `Snap Sell`, or `Snap Both`, or edit the draft values directly.
- Click `Save Adjusted Rule`.
- Expected:
  - only the selected rule changes
  - the draft stays local until `Save Adjusted Rule`
  - quote freshness and allowed band are visible
  - no live order is submitted from this page

## 2. Live Tuning -> prune -> linked order review

- In `Strategy -> Live Tuning`, choose a `PRUNE` symbol that still has linked live orders or reserved balances.
- Open the prune form and confirm the linked-state review table appears.
- Expected when state is clear:
  - `Prune rule only`
  - `Cancel linked orders and prune`
  - `Review in Live Ops`
- Expected when state is unclear or `review required` is present:
  - only `Review in Live Ops` is allowed
  - no prune or cancel happens silently

## 3. Deep Links

- `Compare -> Live Ops`: selected symbol lands in Live Ops focus and manual form context.
- `Compare -> Live Tuning`: selected symbol becomes the tuning focus.
- `Live Tuning -> Live Ops`: focused symbol carries into Live Ops.
- `Live Ops -> Compare`: focused symbol carries into Compare.
- `Live Ops -> Live Tuning`: focused symbol carries into Live Tuning.

## 4. Exit Blocked Flow

- In `Live Ops`, confirm the exit helper is visible when a recent auto-exit slippage block exists.
- Check:
  - latest live price
  - requested sell rate
  - allowed sell band
  - suggested safe sell rate
  - quote freshness
- Click `Use Latest Price (One-time)` and `Use Safe Edge (One-time)` only with a fresh quote.
- Expected:
  - the manual sell form is prefilled
  - saved rule values do not change
  - if the quote is stale, unsafe helper actions stay disabled

## 5. Execution Assistant

- `Snap Buy` changes only `draft buy_below`.
- `Snap Sell` changes only `draft sell_above`.
- `Snap Both` changes both draft values.
- With a stale quote:
  - snap actions are disabled
  - `Save Adjusted Rule` is disabled

## 6. Rules Editor

- Edit one rule `budget_thb` and save it.
- Run bulk budget update for a subset of symbols.
- Expected:
  - single-rule edit changes only that rule
  - bulk update changes only the selected symbols unless `apply all` is enabled

## 7. Reconciliation Review States

- `Symbol Operational State` should show `review required` when reconciliation findings or exchange-order coverage are unclear.
- Confirm review reasons are visible for cases such as partial exchange coverage or exchange query errors.
- From prune flow, confirm unclear exchange state forces review instead of silent prune/cancel.

## Still Manual / Live

These checks still need operator validation against a live or realistic account snapshot:

- real exchange refresh and cancel outcomes
- real quote freshness timing and slippage-band behavior
- actual linked-order cancellation results before prune
- final live manual-order submission behavior
