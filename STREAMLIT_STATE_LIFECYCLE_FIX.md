# Streamlit State Lifecycle Fix - Summary

## Problem
Test `test_live_ops_open_compare_switches_to_strategy_compare` fails in CI:
- **Expected**: `strategy_compare_symbol = "THB_FF"` (navigated from Live Ops)
- **Actual**: `strategy_compare_symbol = "THB_TRX"` (stale pre-seeded state)
- **Root Cause**: Stale Compare workspace state keys were not cleared when Strategy Compare workspace consumed queued navigation from Live Ops

## Solution Overview
Two complementary fixes ensure queued navigation state wins over stale state by clearing all related widget-state keys **before** widgets are created:

1. **Live Ops pre-widget consume** (ops_pages.py): Clear stale Strategy workspace state before any Live Ops widgets render
2. **Strategy Compare state cleanup** (pages.py): Expand queued Compare consume to clear ALL stale Compare keys, not just 2

## Files Changed

### 1. `ui/streamlit/ops_pages.py` - render_live_ops_page()
**Location**: Lines 668-740 (function start)

**What Changed**: Added pre-widget state consume logic at the top of `render_live_ops_page()`, right after early return check for Private API.

**Stale Keys Cleared**:
- `strategy_compare_symbol`
- `strategy_compare_symbol__input`
- `strategy_compare_payload`
- `strategy_compare_symbol__input__signature`
- `strategy_compare_source`
- `strategy_compare_source__input`
- `strategy_compare_resolution`
- `strategy_compare_resolution__input`
- `strategy_compare_days`
- `strategy_compare_days__input`
- `strategy_tuning_focus_symbol`

**Why**: When Live Ops page renders (whether navigated to via queue or refreshed), it must clear any stale Compare/Tuning state that could leak into selectbox widget initialization. This prevents old Compare symbols from showing up in Live Ops manual order forms.

---

### 2. `ui/streamlit/pages.py` - render_strategy_page() Compare consume
**Location**: Lines 640-657 (within queued Compare navigation handler)

**What Changed**: Expanded the queued Compare symbol consume logic to clear **8 additional stale keys** (previously only cleared 2):

**Original** (2 keys):
```python
st.session_state.pop("strategy_compare_payload", None)
st.session_state.pop("strategy_compare_symbol__input__signature", None)
```

**Updated** (10 keys total):
```python
st.session_state.pop("strategy_compare_payload", None)
st.session_state.pop("strategy_compare_symbol__input__signature", None)
st.session_state.pop("strategy_compare_source", None)              # NEW
st.session_state.pop("strategy_compare_source__input", None)       # NEW
st.session_state.pop("strategy_compare_resolution", None)          # NEW
st.session_state.pop("strategy_compare_resolution__input", None)   # NEW
st.session_state.pop("strategy_compare_days", None)                # NEW
st.session_state.pop("strategy_compare_days__input", None)         # NEW
```

**Why**: The Strategy Compare form has 4 input fields (symbol, source, resolution, days), each with both a widget-backed key and an `__input` variant. Pre-seeded test state with `strategy_compare_symbol = "THB_TRX"` would override the queued `strategy_compare_symbol = "THB_FF"` unless ALL related keys are cleared. Without clearing source/resolution/days variants, tests or user actions could still read stale values during subsequent renders.

---

## State Lifecycle Principle
**Before creating any widget, consume queued navigation state and clear ALL related stale state keys.**

This prevents Streamlit's session state from "remembering" old widget values that should be overridden by queued navigation. The pattern:
1. Consume queued navigation key (pop from session state)
2. Clear all widget-backed keys related to that navigation
3. Clear all widget-backed `__input` variants
4. Clear all computed/payload keys derived from the stale state
5. Set ONLY the keys that match the queued target

---

## Testing
Run the full test suite to verify:
```bash
# Single failing test
python -m unittest tests.test_streamlit_strategy_page.TestStrategyPages.test_live_ops_open_compare_switches_to_strategy_compare -v

# All strategy page tests
python -m unittest tests.test_streamlit_strategy_page -v

# Full test suite (CI closest match)
python -m unittest discover -s tests -p "test_*.py" -v
```

Expected behavior after fix:
- ✓ Queued `strategy_compare_symbol` from Live Ops (THB_FF) wins over stale test state (THB_TRX)
- ✓ No widget state policy warnings during render
- ✓ All 3 navigation paths work: Live Ops → Compare, Live Tuning → Compare, Compare refresh

---

## State Keys Reference

### Live Ops Widget Keys
- `live_ops_manual_symbol`
- `live_ops_manual_side`
- `live_ops_manual_order_type`
- `live_ops_manual_amount_thb`
- `live_ops_manual_amount_coin`
- `live_ops_manual_rate`
- `live_ops_manual_confirm`
- `live_ops_selected_order`
- `live_ops_focus_symbol` (queued)

### Strategy Compare Widget Keys
- `strategy_compare_symbol`
- `strategy_compare_symbol__input` (selectbox key)
- `strategy_compare_source`
- `strategy_compare_source__input` (selectbox key)
- `strategy_compare_resolution`
- `strategy_compare_resolution__input` (selectbox key)
- `strategy_compare_days`
- `strategy_compare_days__input` (number_input key)
- `strategy_compare_payload` (computed result)
- `strategy_compare_symbol__input__signature` (widget state signature)
- `strategy_compare_autorun` (queued hydration state)

### Strategy Tuning Widget Keys
- `strategy_tuning_focus_symbol` (queued)
- `strategy_tuning_focus_autorun` (queued hydration)

---

## Key Design Notes
1. **Queued navigation uses separate `__autorun` keys**: Prevents blocking the main widget-backed key during pre-widget consume
2. **Use session_state.get() + str() + .strip() for queued symbols**: Handles None, empty string, and whitespace robustly
3. **Clear BEFORE any st.selectbox/st.form creation**: Ensures fresh widget initialization from clean state
4. **pop(key, None) is safe**: Idempotent operation if key doesn't exist
5. **No mutation after widget creation**: All state consume happens in pre-widget block; widgets only read/write their own keys

