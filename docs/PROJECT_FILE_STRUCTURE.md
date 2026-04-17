# Bitkub Bot - Project File Structure Summary

Generated: April 16, 2026

## Root Level Files

### Configuration Files
- `config.base.json` - Base configuration template
- `config.json` - Main configuration file
- `config.py` - Python configuration module

### Legacy / Ignored Configuration
- `config.prod.override.json` - Ignored production override kept for compatibility

### Main Application Files
- `bitkub_bot.py` - Main bot implementation
- `main.py` - Application entry point
- `streamlit_app.py` - Streamlit web interface

### State & Logging Files
- `runtime_state.json` - Current runtime state
- `state.py` - State management module
- `paper_trade_log.csv` - Paper trading records
- `signal_log.csv` - Trading signal records

### Tracking & Analytics
- `paper_trade_tracker.py` - Paper trading tracker module
- `signal_tracker.py` - Signal tracking module

### Dependencies & Documentation
- `requirements.txt` - Python dependencies
- `README.md` - Project README
- `telegram_test_token.py` - Telegram testing utility

### Virtual Environment
- `env` - Virtual environment directory
- `env_dev` - Development environment directory

---

## Directory Structure

### `/backups/` - Runtime Backup Bundles
- `YYYY/MM/DD/runtime_backup_*.zip` - Timestamped runtime recovery bundle
- `manifest.json` inside each bundle - Captured assets and restore targets

### `/clients/` - API Clients
- `__init__.py` - Package initializer
- `bitkub_client.py` - Public Bitkub API client
- `bitkub_private_client.py` - Private Bitkub API client

### `/core/` - Core Business Logic
- `__init__.py` - Package initializer
- `strategy.py` - Trading strategy implementation
- `trade_engine.py` - Trade execution engine

### `/data/` - Data Files & Databases
- `bitkub_open_orders_probe_summary.json` - Open orders probe data
- `bitkub_paper_clean.db-journal` - Clean paper trade DB journal
- `bitkub_paper_fresh.db-journal` - Fresh paper trade DB journal
- `bitkub_paper_probe.db-journal` - Probe paper trade DB journal
- `sqlite_scratch_test.db-journal` - Scratch test DB journal

### `/deploy/` - Deployment Configuration
- `BRANCH_PROTECTION_CHECKLIST.md` - Branch protection guidelines
- `DEPLOY_SECRETS_CHECKLIST.md` - Secrets management checklist
- `Caddyfile` - Reverse proxy configuration for the Docker stack
- `VPS_DEPLOY.md` - VPS deployment documentation
- `deploy_prod.sh` - Docker Compose deploy script for the VPS

### `/deploy/archive/systemd/` - Legacy Systemd Units
- `README.md` - Legacy deployment archive index
- `bitkub-engine.service` - Archived engine unit
- `bitkub-streamlit.service` - Archived Streamlit unit

### `/runtime/` - Docker Runtime State
- `config.json` - Mutable production config override on the VPS
- `runtime_state.json` - Engine runtime state
- `signal_log.csv` - Trading signal log
- `paper_trade_log.csv` - Paper trade log

### `/docs/` - Documentation
- `CI_CD_RUNBOOK.md` - CI/CD operational guide
- `archive/README.md` - Archive index for historical notes
- `generate_new_ssh_key_github_actions.md` - SSH key generation guide
- `PROJECT_FILE_STRUCTURE.md` - This file

### `/docs/archive/` - Historical Notes
- `README.md` - Archive index for historical notes
- `bitkub_bot_docker_deploy_review_summary.md` - Docker deploy review summary
- `pr-fixbug-initial-bug-investigation.md` - Historical bug investigation notes

### `/scripts/` - Automation Scripts
- `start_engine.sh` - Engine startup script
- `start_streamlit.sh` - Streamlit startup script
- `backup_runtime.py` - Runtime backup helper
- `restore_runtime.py` - Runtime restore helper

### `/services/` - Business Services (15 modules)
- `__init__.py` - Package initializer
- `account_service.py` - Account management
- `alert_service.py` - Alert/notification handling
- `db_service.py` - Database operations
- `env_service.py` - Environment configuration service
- `execution_service.py` - Trade execution service
- `log_service.py` - Logging service
- `market_symbol_service.py` - Market symbol management
- `order_service.py` - Order management
- `reconciliation_service.py` - Account reconciliation
- `state_service.py` - State management
- `stats_service.py` - Statistics calculation
- `strategy_lab_service.py` - Strategy laboratory/testing
- `telegram_service.py` - Telegram bot integration
- `ui_service.py` - UI service
- `version_service.py` - Version management

### `/tests/` - Test Suite
- `test_config_watchlist_and_reload_policy.py` - Configuration reload tests
- `test_daily_metrics_reporting.py` - Daily metrics tests
- `test_execution_order_refresh.py` - Order execution tests
- `test_market_symbol_service.py` - Market symbol service tests
- `test_open_orders_support_probe.py` - Open orders probe tests
- `test_reporting_shadow_mode.py` - Shadow mode reporting tests
- `test_streamlit_strategy_page.py` - Streamlit UI tests
- `test_telegram_positions_command.py` - Telegram command tests
- `test_telegram_reload_and_prune_revalidation.py` - Telegram reload tests
- `test_validation_framework.py` - Validation framework tests

### `/ui/` - User Interface
- `__init__.py` - Package initializer
- `/streamlit/` - Streamlit web interface
  - `__init__.py` - Package initializer
  - `actions.py` - UI action handlers
  - `app.py` - Main Streamlit app
  - `config_support.py` - Configuration UI support
  - `data.py` - Data display helpers
  - `diagnostics_support.py` - Diagnostics UI support
  - `ops_pages.py` - Operations pages
  - `pages.py` - Page definitions
  - `refresh.py` - Refresh logic
  - `strategy_support.py` - Strategy UI support
  - `styles.py` - UI styling

### `/utils/` - Utility Modules
- `__init__.py` - Package initializer
- `time_utils.py` - Time-related utilities

---

## Summary Statistics

| Category | Count |
|----------|-------|
| Root Configuration/State Files | 8 |
| Root Source Files | 3 |
| Root Support Files | 2 |
| Directories | 12 |
| Service Modules | 16 |
| UI Components | 11 |
| Test Files | 8 |
| Documentation Files | 6 |

## Key Components

1. **API Integration** - Bitkub exchange integration via `bitkub_client.py` and `bitkub_private_client.py`
2. **Trading Engine** - Core trading logic in `/core/` with strategy execution
3. **Web Interface** - Streamlit-based dashboard in `/ui/streamlit/`
4. **Services Layer** - Modular service architecture for separation of concerns (15 services)
5. **Testing** - Comprehensive test suite with 8 test modules
6. **Deployment** - Production deployment configs and Docker Compose runtime
7. **Documentation** - CI/CD, deployment, and historical archive notes
