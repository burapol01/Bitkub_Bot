# PR Draft

## Title

`Add Streamlit control plane, Telegram ops, and paper-mode hardening`

## Description

```md
## Summary
- add a multi-page Streamlit control plane for overview, account, live ops, strategy, reports, diagnostics, logs, and config editing
- add Telegram notification and command-control flows with confirmation, safer replies, and better polling resilience
- harden paper-mode setup, SQLite handling, reporting, and Streamlit workflow bugs for day-to-day testing
- add VPS deployment helpers including startup scripts, systemd units, and deployment documentation

## What Changed
- refactor the Streamlit UI into focused modules under `ui/streamlit/`
- add live ops, diagnostics, reports, strategy compare/tuning, config editing, and refresh controls
- add Telegram outbox/command logging, confirmation flow, chunked responses, and improved error handling
- add execution, reconciliation, order, and strategy helpers used by both console and Streamlit workflows
- support local env fallback and safer paper DB setup for local testing
- add `telegram_test_token.py` and expand README/deploy documentation
- fix recent Streamlit workflow issues:
  - auto-refresh checkbox state sticking across pages
  - missing daily PnL summary in Reports
  - ranked symbol promotion flow not applying reliably
  - compare variant dropdown state not resetting cleanly when the compare scope changes

## Validation
- verified the new DB reporting import path loads correctly
- smoke-started Streamlit successfully from `.venv`
- confirmed the latest Streamlit fixes in the local workspace
- ran local code/import checks for the updated UI and DB modules

## Notes
- this branch includes both feature work and follow-up bug fixes, so the PR is intentionally broader than a single hotfix
- local smoke-test temp files were removed and are not part of the branch
```
