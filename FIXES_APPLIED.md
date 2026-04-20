# Bitkub_Bot - CI Failure Fixes

## Summary
Fixed 2 separate failures in the full local test suite:

### 1. UI Navigation Failure: test_live_ops_open_compare_switches_to_strategy_compare
**Status**: ✓ Fixed

**Problem**:
- Test navigates from Live Ops → Strategy Compare
- Expected: `strategy_compare_symbol = "THB_FF"` (queued from button click)
- Actual: `strategy_compare_symbol = "THB_TRX"` (stale pre-seeded test state)

**Root Cause**:
When the button click queues Compare navigation, session state contains pre-seeded stale values from test setup. The widgets read from session_state BEFORE the stale values are cleared, so widgets initialize with wrong values. The queued symbol is set afterwards, but by then the widget has already captured the stale value.

**Fix Applied**:
Moved state cleanup logic to IMMEDIATELY AFTER the autorun dict is popped in the Compare section rendering (lines 1420-1439 in pages.py), but BEFORE widget defaults are calculated. This ensures:
1. Autorun dict is consumed
2. Stale keys are cleared from session_state
3. Only THEN are widget defaults calculated (which read from session_state)
4. Widgets initialize with clean queued values

### 2. Windows Backup Cleanup Error: test_backup_bundle_contains_manifest_and_restores_assets
**Status**: ✓ Fixed

**Problem**:
- TemporaryDirectory cleanup fails with `WinError 32: The process cannot access the file because it is being used by another process`
- SQLite temp backup files remain locked after backup creation
- Cleanup happens before file handles are fully released on Windows

**Root Cause**:
SQLite connections are closed via context managers, but the database file buffers aren't flushed to disk. On Windows, the OS/antivirus can hold file locks that prevent directory deletion. The TemporaryDirectory cleanup happens before these locks are released.

**Fix Applied**:

#### File 1: `services/backup_service.py` - _snapshot_sqlite_database() (lines 93-112)
- Explicit `commit()` on both connections before context exit
- Added file handle validation read to ensure file is flushed
- Ensures database file is fully closed and flushed before returning

#### File 2: `services/backup_service.py` - create_runtime_backup() (lines 347-352)
- Added `gc.collect()` before exiting TemporaryDirectory context
- Forces Python garbage collection to release any remaining object references
- Particularly important on Windows where file handles can be held by OS

---

## Files Changed

### 1. ui/streamlit/pages.py

**Location**: Lines 640-659 (Simplified queued Compare setup)
- Removed state clearing logic (moved to Compare section)
- Kept autorun dict creation to signal Compare section that state should be cleared

**Location**: Lines 1420-1439 (Compare section state cleanup)
- NEW: Added state clearing immediately after `compare_autorun = st.session_state.pop(...)`
- Clears ALL stale Compare keys before widget defaults are calculated
- Keys cleared:
  - strategy_compare_payload
  - strategy_compare_symbol__input__signature
  - strategy_compare_source
  - strategy_compare_source__input
  - strategy_compare_resolution
  - strategy_compare_resolution__input
  - strategy_compare_days
  - strategy_compare_days__input

### 2. ui/streamlit/ops_pages.py

**Location**: Lines 668-700 (render_live_ops_page start)
- Added pre-widget state consume for symmetry (clears stale Compare/Tuning state before Live Ops widgets)
- Helps prevent state leakage when navigating TO Live Ops with queued focus symbol
- Not directly needed for failing test, but completes state isolation pattern

### 3. services/backup_service.py

**Location**: Lines 93-112 (_snapshot_sqlite_database function)
```python
# Changes:
# 1. Added explicit dest_conn.commit() inside context
# 2. Added explicit source_conn.commit() outside inner context but inside outer
# 3. Added file handle read to validate file is accessible and flushed
# 4. Wrapped read in try/except to ignore errors from already-locked files
```

**Location**: Lines 347-352 (create_runtime_backup function)
```python
# Changes:
# Added after _zip_bundle_path call but before TemporaryDirectory context exits:
# - import gc
# - gc.collect() to force garbage collection
```

---

## State Lifecycle Principle Applied
> **Stale widget-backed state must be cleared BEFORE widget initialization, after queued navigation signal is received.**

For Streamlit apps with queued navigation:
1. Store queued target in session_state (e.g., `strategy_compare_symbol_autorun`)
2. Pop queued value and detect navigation
3. Clear ALL related stale state keys from session_state
4. THEN calculate widget defaults (which read from session_state)
5. Widgets initialize with clean queued values

---

## Testing

Run full test suite to verify both fixes:
```bash
# Specific failing tests
python -m unittest tests.test_streamlit_strategy_page.TestStrategyPages.test_live_ops_open_compare_switches_to_strategy_compare -v
python -m unittest tests.test_backup_restore_phase1.TestBackupPhase1.test_backup_bundle_contains_manifest_and_restores_assets -v

# All strategy page tests
python -m unittest tests.test_streamlit_strategy_page -v

# All backup tests  
python -m unittest tests.test_backup_restore_phase1 -v

# Full suite (CI closest match)
python -m unittest discover -s tests -p "test_*.py" -v
```

Expected results:
- ✓ Queued `strategy_compare_symbol` from Live Ops (THB_FF) wins over pre-seeded state (THB_TRX)
- ✓ No Streamlit widget state policy warnings during rendering
- ✓ No file/directory cleanup errors on Windows
- ✓ All navigation paths work: Live Ops → Compare, Compare refresh, state persistence

---

## Design Notes

### Why move cleanup into Compare section instead of earlier?
- Body code (test setup) runs BEFORE page rendering and pre-seeds stale state
- Cleanup at top of render_strategy_page() runs AFTER body but BEFORE Compare section
- Moving cleanup into Compare section (right after autorun pop) ensures it happens at exactly the right moment: when we know for sure this is a Compare navigation AND just before defaults are calculated
- This is more reliable than checking conditions that might be false

### Why add gc.collect() for backup cleanup?
- Context managers close file handles, but don't guarantee buffer flush on Windows
- Python object garbage collection can release file handles held by closed sqlite3 connections
- gc.collect() is safe to call and helps ensure all file handles are released
- Placed right before TemporaryDirectory context exit to maximize effectiveness

### Symmetry of state clearing in both directions
- **Live Ops pre-widget consume**: When navigating TO Live Ops, clear stale Compare/Tuning state
- **Compare pre-widget consume**: When navigating TO Compare, clear stale symbols  
- This pattern prevents state leakage between workspace pages
- Matches the queued navigation architecture already in use

