# Retention Archive Phase 1

Phase 1 keeps SQLite as the live runtime database and introduces archive-before-delete retention for analytical history.

## What changed

- `market_snapshots`, `signal_logs`, `account_snapshots`, and `reconciliation_results` now use hot retention plus disk archives
- archives are written as gzip-compressed CSV under `archive_dir`
- runtime events remain short-lived and are still pruned directly
- archive runs are recorded in the SQLite `retention_archive_runs` table

## Config keys

- `archive_enabled`
- `archive_dir`
- `archive_format`
- `archive_compression`
- `market_snapshot_archive_enabled`
- `signal_log_archive_enabled`
- `account_snapshot_archive_enabled`
- `reconciliation_archive_enabled`
- `market_snapshot_hot_retention_days`
- `signal_log_hot_retention_days`
- `account_snapshot_hot_retention_days`
- `reconciliation_hot_retention_days`
- `runtime_event_retention_days`

## Operational model

- records stay in SQLite during the hot window
- once a full day is older than the configured hot retention, the engine writes that day to an archive file first
- only after the archive file is safely written does cleanup remove the source rows from SQLite
- if archive writing fails, source rows are left untouched

## Restore / inspect

- archived data lives on disk in date-partitioned CSV.GZ files
- the `retention_archive_runs` table tells you which date range was archived, how many rows moved, and whether cleanup completed
- to restore, load the archive files back into SQLite or a separate analysis database
