# Bitkub Bot Backup and Restore Runbook

This project keeps SQLite as the live runtime store. Backups are meant for operational recovery after a bad deploy, VPS issue, file corruption, or accidental operator mistake.

## What gets backed up

- SQLite runtime database: `data/bitkub.db`
- SQLite WAL-safe snapshot captured through the SQLite backup API
- Runtime state file(s): `runtime_state.json` and `runtime_state.pending.json` when present
- Active config files: `config.json` and `config.base.json` when present
- Optional `.env` file when `backup_include_env_file=true`
- Backup manifest with captured asset list, sizes, and status

## What does not get backed up

- The long-term analytical archive files under `archive_dir`
- External services, exchange data, or Telegram history outside the local files above

## Backup directory

Backups are written under:

```text
backups/YYYY/MM/DD/runtime_backup_YYYYMMDD_HHMMSS.zip
```

The root directory is configurable with `backup_dir`.

## Config options

- `backup_dir`
  - Default: `backups`
- `backup_retention_days`
  - Default: `90`
  - Backups older than this are pruned after a successful backup run
- `backup_include_env_file`
  - Default: `false`
  - When enabled, the loaded `.env` file is copied into the bundle if one is available

## Run backup

From the project root:

```powershell
python scripts/backup_runtime.py
```

Useful overrides:

```powershell
python scripts/backup_runtime.py --backup-dir backups --retention-days 90
python scripts/backup_runtime.py --include-env
```

The backup command prints:

- bundle path
- bundle size
- captured / failed / skipped asset counts
- warnings and errors
- pruned backup files, if any

## Restore workflow

Restore should be done with the bot and Streamlit stopped.

1. Stop the engine and UI processes.
2. Pick a known-good backup bundle.
3. Restore with the helper script.
4. Restart the bot and verify the config, runtime state, and DB health.

Example:

```powershell
python scripts/restore_runtime.py --bundle backups/2026/04/17/runtime_backup_20260417_153000.zip --overwrite
```

Restore safeguards:

- the helper refuses to overwrite existing files unless `--overwrite` is set
- extraction happens into a temporary staging directory first
- the helper reports any missing or invalid bundle entries before applying changes

## After restore

- restart the bot process
- reload or restart Streamlit
- verify that `config.json`, `runtime_state.json`, and `data/bitkub.db` look correct
- confirm the latest backup and retention status in the Diagnostics page

## Downtime expectation

Backup is online-safe for SQLite WAL mode.

Restore should be treated as downtime work because runtime files and the SQLite database will be replaced.

## Quick inspection

- The Diagnostics page shows the latest backup timestamp, location, and size
- The backup manifest inside each bundle records the asset list and restore targets
- `scripts/restore_runtime.py` is the supported helper for restoring a bundle
