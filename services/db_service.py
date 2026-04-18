import csv
import gzip
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from typing import Any

from services.env_service import get_env_path
from utils.time_utils import coerce_time_text, format_date_text, format_time_text, now_dt

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "archive"
DB_PATH = get_env_path("BITKUB_DB_PATH", DEFAULT_DB_DIR / "bitkub.db")
DB_DIR = DB_PATH.parent
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = int(SQLITE_TIMEOUT_SECONDS * 1000)

RETENTION_ARCHIVE_FORMAT_CSV = "csv"
RETENTION_ARCHIVE_COMPRESSION_GZIP = "gzip"

RETENTION_ARCHIVE_TABLES: dict[str, dict[str, str]] = {
    "market_snapshots": {
        "timestamp_column": "created_at",
        "hot_retention_key": "market_snapshot_hot_retention_days",
        "archive_enabled_key": "market_snapshot_archive_enabled",
    },
    "signal_logs": {
        "timestamp_column": "created_at",
        "hot_retention_key": "signal_log_hot_retention_days",
        "archive_enabled_key": "signal_log_archive_enabled",
    },
    "account_snapshots": {
        "timestamp_column": "created_at",
        "hot_retention_key": "account_snapshot_hot_retention_days",
        "archive_enabled_key": "account_snapshot_archive_enabled",
    },
    "reconciliation_results": {
        "timestamp_column": "created_at",
        "hot_retention_key": "reconciliation_hot_retention_days",
        "archive_enabled_key": "reconciliation_archive_enabled",
    },
}

RETENTION_RUNTIME_TABLES: dict[str, dict[str, str]] = {
    "runtime_events": {
        "timestamp_column": "created_at",
        "retention_key": "runtime_event_retention_days",
    },
    "trade_journal": {
        "timestamp_column": "created_at",
        "retention_key": "runtime_event_retention_days",
    },
    "validation_runs": {
        "timestamp_column": "created_at",
        "retention_key": "runtime_event_retention_days",
    },
    "validation_run_slices": {
        "timestamp_column": "test_end_at",
        "retention_key": "runtime_event_retention_days",
    },
    "validation_consistency_checks": {
        "timestamp_column": "created_at",
        "retention_key": "runtime_event_retention_days",
    },
}


def configure_sqlite_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _connect() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    return configure_sqlite_connection(conn)


def init_db():
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT
            );

            CREATE TABLE IF NOT EXISTS signal_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                last_price REAL NOT NULL,
                buy_below REAL NOT NULL,
                sell_above REAL NOT NULL,
                zone TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                last_price REAL NOT NULL,
                buy_below REAL NOT NULL,
                sell_above REAL NOT NULL,
                zone TEXT NOT NULL,
                status TEXT NOT NULL,
                trading_mode TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                resolution TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open_at TEXT NOT NULL,
                open_price REAL NOT NULL,
                high_price REAL NOT NULL,
                low_price REAL NOT NULL,
                close_price REAL NOT NULL,
                volume REAL NOT NULL,
                UNIQUE(symbol, resolution, open_time)
            );

            CREATE TABLE IF NOT EXISTS paper_trade_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buy_time TEXT NOT NULL,
                sell_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exit_reason TEXT NOT NULL,
                budget_thb REAL NOT NULL,
                buy_fee_thb REAL NOT NULL,
                net_budget_thb REAL NOT NULL,
                buy_price REAL NOT NULL,
                sell_price REAL NOT NULL,
                coin_qty REAL NOT NULL,
                gross_proceeds_thb REAL NOT NULL,
                sell_fee_thb REAL NOT NULL,
                net_proceeds_thb REAL NOT NULL,
                pnl_thb REAL NOT NULL,
                pnl_percent REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source TEXT NOT NULL,
                private_api_status TEXT NOT NULL,
                capabilities_json TEXT,
                snapshot_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reconciliation_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL,
                warnings_json TEXT,
                positions_count INTEGER NOT NULL,
                exchange_balances_json TEXT
            );

            CREATE TABLE IF NOT EXISTS retention_archive_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                table_name TEXT NOT NULL,
                archive_date TEXT NOT NULL,
                archive_start_at TEXT NOT NULL,
                archive_end_at TEXT NOT NULL,
                hot_retention_days INTEGER NOT NULL,
                archive_dir TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                archive_format TEXT NOT NULL,
                archive_compression TEXT NOT NULL,
                record_count INTEGER NOT NULL DEFAULT 0,
                archive_status TEXT NOT NULL,
                cleanup_status TEXT NOT NULL,
                cleanup_deleted_count INTEGER NOT NULL DEFAULT 0,
                cleanup_completed_at TEXT,
                error_message TEXT,
                UNIQUE(table_name, archive_date)
            );

            CREATE TABLE IF NOT EXISTS execution_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                order_type TEXT NOT NULL,
                state TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT,
                guardrails_json TEXT,
                exchange_order_id TEXT,
                exchange_client_id TEXT,
                message TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS execution_order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                execution_order_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                event_type TEXT NOT NULL,
                message TEXT NOT NULL,
                details_json TEXT,
                FOREIGN KEY (execution_order_id) REFERENCES execution_orders(id)
            );

            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                trading_mode TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT,
                signal_reason TEXT,
                exit_reason TEXT,
                request_rate REAL,
                latest_price REAL,
                amount_thb REAL,
                amount_coin REAL,
                details_json TEXT
            );

            CREATE TABLE IF NOT EXISTS strategy_daily_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL,
                strategy_key TEXT NOT NULL,
                trading_mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                closed_trades INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                losses INTEGER NOT NULL,
                realized_pnl_thb REAL NOT NULL,
                fee_thb REAL NOT NULL,
                turnover_thb REAL NOT NULL,
                gross_win_thb REAL NOT NULL,
                gross_loss_thb REAL NOT NULL,
                gross_pnl_before_fees_thb REAL NOT NULL,
                avg_pnl_thb REAL NOT NULL,
                avg_fee_thb REAL NOT NULL,
                win_rate_percent REAL NOT NULL,
                profit_factor REAL NOT NULL,
                UNIQUE(report_date, strategy_key, symbol)
            );

            CREATE TABLE IF NOT EXISTS portfolio_daily_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL UNIQUE,
                strategies_active INTEGER NOT NULL,
                symbols_active INTEGER NOT NULL,
                paper_closed_trades INTEGER NOT NULL,
                live_closed_trades INTEGER NOT NULL,
                combined_closed_trades INTEGER NOT NULL,
                paper_wins INTEGER NOT NULL,
                paper_losses INTEGER NOT NULL,
                live_wins INTEGER NOT NULL,
                live_losses INTEGER NOT NULL,
                combined_wins INTEGER NOT NULL,
                combined_losses INTEGER NOT NULL,
                paper_realized_pnl_thb REAL NOT NULL,
                live_realized_pnl_thb REAL NOT NULL,
                combined_realized_pnl_thb REAL NOT NULL,
                paper_fee_thb REAL NOT NULL,
                live_fee_thb REAL NOT NULL,
                combined_fee_thb REAL NOT NULL,
                paper_turnover_thb REAL NOT NULL,
                live_turnover_thb REAL NOT NULL,
                combined_turnover_thb REAL NOT NULL,
                paper_gross_win_thb REAL NOT NULL,
                paper_gross_loss_thb REAL NOT NULL,
                live_gross_win_thb REAL NOT NULL,
                live_gross_loss_thb REAL NOT NULL,
                combined_gross_win_thb REAL NOT NULL,
                combined_gross_loss_thb REAL NOT NULL,
                paper_gross_pnl_before_fees_thb REAL NOT NULL,
                live_gross_pnl_before_fees_thb REAL NOT NULL,
                combined_gross_pnl_before_fees_thb REAL NOT NULL,
                paper_win_rate_percent REAL NOT NULL,
                live_win_rate_percent REAL NOT NULL,
                combined_win_rate_percent REAL NOT NULL,
                paper_profit_factor REAL NOT NULL,
                live_profit_factor REAL NOT NULL,
                combined_profit_factor REAL NOT NULL,
                cumulative_realized_pnl_thb REAL NOT NULL,
                peak_cumulative_realized_pnl_thb REAL NOT NULL,
                drawdown_thb REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS validation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                validation_type TEXT NOT NULL,
                status TEXT NOT NULL,
                symbol TEXT NOT NULL,
                data_source TEXT NOT NULL,
                resolution TEXT,
                mode TEXT NOT NULL,
                date_from TEXT NOT NULL,
                date_to TEXT NOT NULL,
                train_window_days INTEGER NOT NULL,
                test_window_days INTEGER NOT NULL,
                step_days INTEGER NOT NULL,
                fee_rate REAL NOT NULL,
                cooldown_seconds INTEGER NOT NULL,
                base_rule_json TEXT NOT NULL,
                summary_json TEXT,
                metadata_json TEXT
            );

            CREATE TABLE IF NOT EXISTS validation_run_slices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                validation_run_id INTEGER NOT NULL,
                slice_no INTEGER NOT NULL,
                status TEXT NOT NULL,
                train_start_at TEXT NOT NULL,
                train_end_at TEXT NOT NULL,
                test_start_at TEXT NOT NULL,
                test_end_at TEXT NOT NULL,
                selected_variant TEXT,
                selected_rule_json TEXT,
                train_metrics_json TEXT,
                test_metrics_json TEXT,
                train_result_hash TEXT,
                test_result_hash TEXT,
                notes_json TEXT,
                FOREIGN KEY (validation_run_id) REFERENCES validation_runs(id)
            );

            CREATE TABLE IF NOT EXISTS validation_consistency_checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                validation_run_id INTEGER,
                check_type TEXT NOT NULL,
                status TEXT NOT NULL,
                symbol TEXT NOT NULL,
                data_source TEXT NOT NULL,
                resolution TEXT,
                window_start_at TEXT NOT NULL,
                window_end_at TEXT NOT NULL,
                rule_json TEXT NOT NULL,
                details_json TEXT,
                FOREIGN KEY (validation_run_id) REFERENCES validation_runs(id)
            );

            CREATE TABLE IF NOT EXISTS telegram_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                payload_json TEXT,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS telegram_command_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                update_id INTEGER NOT NULL UNIQUE,
                chat_id TEXT NOT NULL,
                username TEXT,
                command_text TEXT NOT NULL,
                status TEXT NOT NULL,
                response_text TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                action_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_id TEXT,
                source TEXT,
                target_type TEXT,
                target_id TEXT,
                symbol TEXT,
                old_value_json TEXT,
                new_value_json TEXT,
                status TEXT NOT NULL,
                message TEXT NOT NULL,
                reason TEXT,
                correlation_id TEXT,
                metadata_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_market_snapshots_created_symbol_id
            ON market_snapshots(created_at, symbol, id);

            CREATE INDEX IF NOT EXISTS idx_signal_logs_created_symbol
            ON signal_logs(created_at, symbol);

            CREATE INDEX IF NOT EXISTS idx_paper_trade_logs_sell_symbol
            ON paper_trade_logs(sell_time, symbol);

            CREATE INDEX IF NOT EXISTS idx_execution_orders_state_id
            ON execution_orders(state, id);

            CREATE INDEX IF NOT EXISTS idx_runtime_events_severity_id
            ON runtime_events(severity, id);

            CREATE INDEX IF NOT EXISTS idx_trade_journal_created_symbol
            ON trade_journal(created_at, symbol, id);

            CREATE INDEX IF NOT EXISTS idx_strategy_daily_metrics_report_symbol
            ON strategy_daily_metrics(report_date, symbol, strategy_key, id);

            CREATE INDEX IF NOT EXISTS idx_portfolio_daily_metrics_report_date
            ON portfolio_daily_metrics(report_date, id);

            CREATE INDEX IF NOT EXISTS idx_validation_runs_created_symbol
            ON validation_runs(created_at, symbol, id);

            CREATE INDEX IF NOT EXISTS idx_validation_run_slices_run_slice
            ON validation_run_slices(validation_run_id, slice_no, id);

            CREATE INDEX IF NOT EXISTS idx_validation_consistency_checks_created_symbol
            ON validation_consistency_checks(created_at, symbol, id);

            CREATE INDEX IF NOT EXISTS idx_retention_archive_runs_table_date
            ON retention_archive_runs(table_name, archive_date DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_retention_archive_runs_completed_at
            ON retention_archive_runs(completed_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
            ON audit_events(created_at DESC, id DESC);

            CREATE INDEX IF NOT EXISTS idx_audit_events_action_status
            ON audit_events(action_type, status, id DESC);

            CREATE INDEX IF NOT EXISTS idx_audit_events_symbol
            ON audit_events(symbol, id DESC);
            """
        )


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


def _normalize_time_text(value: Any) -> str:
    return coerce_time_text(value)


def _day_range_bounds(day_text: str) -> tuple[str, str]:
    day = datetime.strptime(str(day_text), "%Y-%m-%d")
    next_day = day + timedelta(days=1)
    return (
        f"{day.strftime('%Y-%m-%d')} 00:00:00",
        f"{next_day.strftime('%Y-%m-%d')} 00:00:00",
    )


def insert_runtime_event(
    *,
    created_at: str,
    event_type: str,
    severity: str,
    message: str,
    details: Any = None,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_events (
                created_at,
                event_type,
                severity,
                message,
                details_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (_normalize_time_text(created_at), event_type, severity, message, _to_json(details)),
        )


def insert_audit_event(
    *,
    created_at: str,
    action_type: str,
    actor_type: str,
    actor_id: str | None = None,
    source: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    symbol: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    status: str,
    message: str,
    reason: str | None = None,
    correlation_id: str | None = None,
    metadata: Any = None,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_events (
                created_at,
                action_type,
                actor_type,
                actor_id,
                source,
                target_type,
                target_id,
                symbol,
                old_value_json,
                new_value_json,
                status,
                message,
                reason,
                correlation_id,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                str(action_type),
                str(actor_type),
                str(actor_id) if actor_id is not None else None,
                str(source) if source is not None else None,
                str(target_type) if target_type is not None else None,
                str(target_id) if target_id is not None else None,
                str(symbol) if symbol is not None else None,
                _to_json(old_value),
                _to_json(new_value),
                str(status),
                str(message),
                str(reason) if reason is not None else None,
                str(correlation_id) if correlation_id is not None else None,
                _to_json(metadata),
            ),
        )
    return int(cursor.lastrowid)


def insert_telegram_outbox(
    *,
    created_at: str,
    event_type: str,
    title: str,
    body: str,
    payload: Any = None,
    status: str = "queued",
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO telegram_outbox (
                created_at,
                event_type,
                title,
                body,
                payload_json,
                status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                event_type,
                title,
                body,
                _to_json(payload),
                status,
            ),
        )


def insert_telegram_command_log(
    *,
    created_at: str,
    update_id: int,
    chat_id: str,
    username: str | None,
    command_text: str,
    status: str,
    response_text: str | None = None,
) -> int | None:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO telegram_command_log (
                created_at,
                update_id,
                chat_id,
                username,
                command_text,
                status,
                response_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                int(update_id),
                str(chat_id),
                username,
                str(command_text),
                str(status),
                response_text,
            ),
        )
        if int(cursor.rowcount or 0) <= 0:
            return None
        return int(cursor.lastrowid)


def update_telegram_command_log(
    *,
    command_log_id: int,
    status: str,
    response_text: str | None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE telegram_command_log
            SET status = ?, response_text = ?
            WHERE id = ?
            """,
            (str(status), response_text, int(command_log_id)),
        )


def expire_stale_telegram_command_logs(*, created_before: str) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE telegram_command_log
            SET status = 'expired',
                response_text = CASE
                    WHEN COALESCE(response_text, '') = '' THEN 'Telegram confirmation expired.'
                    WHEN instr(response_text, 'Telegram confirmation expired.') > 0 THEN response_text
                    ELSE response_text || char(10) || 'Telegram confirmation expired.'
                END
            WHERE status = 'pending_confirmation'
              AND created_at <= ?
            """,
            (_normalize_time_text(created_before),),
        )
        return int(cursor.rowcount or 0)


def insert_signal_log(
    *,
    created_at: str,
    symbol: str,
    last_price: float,
    buy_below: float,
    sell_above: float,
    zone: str,
    status: str,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO signal_logs (
                created_at,
                symbol,
                last_price,
                buy_below,
                sell_above,
                zone,
                status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                symbol,
                last_price,
                buy_below,
                sell_above,
                zone,
                status,
            ),
        )


def insert_market_snapshot(
    *,
    created_at: str,
    symbol: str,
    last_price: float,
    buy_below: float,
    sell_above: float,
    zone: str,
    status: str,
    trading_mode: str,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                created_at,
                symbol,
                last_price,
                buy_below,
                sell_above,
                zone,
                status,
                trading_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                symbol,
                last_price,
                buy_below,
                sell_above,
                zone,
                status,
                trading_mode,
            ),
        )


def upsert_market_candles(
    *,
    symbol: str,
    resolution: str,
    candles: list[dict[str, Any]],
):
    if not candles:
        return

    with _connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO market_candles (
                symbol,
                resolution,
                open_time,
                open_at,
                open_price,
                high_price,
                low_price,
                close_price,
                volume
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    symbol,
                    resolution,
                    int(candle["open_time"]),
                    _normalize_time_text(candle["open_at"]),
                    float(candle["open_price"]),
                    float(candle["high_price"]),
                    float(candle["low_price"]),
                    float(candle["close_price"]),
                    float(candle["volume"]),
                )
                for candle in candles
            ],
        )


def fetch_market_candles(
    *,
    symbol: str,
    resolution: str,
    lookback_days: int,
) -> list[dict[str, Any]]:
    cutoff_text = format_time_text(now_dt() - timedelta(days=lookback_days))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT symbol, resolution, open_time, open_at, open_price, high_price,
                   low_price, close_price, volume
            FROM market_candles
            WHERE symbol = ? AND resolution = ? AND open_at >= ?
            ORDER BY open_time ASC
            """,
            (symbol, resolution, cutoff_text),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_market_candle_coverage(*, resolution: str | None = None) -> list[dict[str, Any]]:
    resolution_clause = "WHERE resolution = ?" if resolution else ""
    params = (resolution,) if resolution else ()
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT symbol, resolution, COUNT(*) AS candles,
                   MIN(open_at) AS first_seen,
                   MAX(open_at) AS last_seen,
                   MIN(low_price) AS min_price,
                   MAX(high_price) AS max_price
            FROM market_candles
            {resolution_clause}
            GROUP BY symbol, resolution
            ORDER BY symbol, resolution
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def insert_paper_trade_log(
    *,
    buy_time: str,
    sell_time: str,
    symbol: str,
    exit_reason: str,
    budget_thb: float,
    buy_fee_thb: float,
    net_budget_thb: float,
    buy_price: float,
    sell_price: float,
    coin_qty: float,
    gross_proceeds_thb: float,
    sell_fee_thb: float,
    net_proceeds_thb: float,
    pnl_thb: float,
    pnl_percent: float,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO paper_trade_logs (
                buy_time,
                sell_time,
                symbol,
                exit_reason,
                budget_thb,
                buy_fee_thb,
                net_budget_thb,
                buy_price,
                sell_price,
                coin_qty,
                gross_proceeds_thb,
                sell_fee_thb,
                net_proceeds_thb,
                pnl_thb,
                pnl_percent
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(buy_time),
                _normalize_time_text(sell_time),
                symbol,
                exit_reason,
                budget_thb,
                buy_fee_thb,
                net_budget_thb,
                buy_price,
                sell_price,
                coin_qty,
                gross_proceeds_thb,
                sell_fee_thb,
                net_proceeds_thb,
                pnl_thb,
                pnl_percent,
            ),
        )


def insert_account_snapshot(
    *,
    created_at: str,
    source: str,
    private_api_status: str,
    capabilities: list[str] | None,
    snapshot: dict,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO account_snapshots (
                created_at,
                source,
                private_api_status,
                capabilities_json,
                snapshot_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                source,
                private_api_status,
                _to_json(capabilities),
                _to_json(snapshot),
            ),
        )


def insert_reconciliation_result(
    *,
    created_at: str,
    phase: str,
    status: str,
    warnings: list[str],
    positions_count: int,
    exchange_balances: dict[str, float],
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO reconciliation_results (
                created_at,
                phase,
                status,
                warnings_json,
                positions_count,
                exchange_balances_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                phase,
                status,
                _to_json(warnings),
                positions_count,
                _to_json(exchange_balances),
            ),
        )


def insert_execution_order(
    *,
    created_at: str,
    updated_at: str,
    symbol: str,
    side: str,
    order_type: str,
    state: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any] | None,
    guardrails: dict[str, Any] | None,
    exchange_order_id: str | None,
    exchange_client_id: str | None,
    message: str,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO execution_orders (
                created_at,
                updated_at,
                symbol,
                side,
                order_type,
                state,
                request_json,
                response_json,
                guardrails_json,
                exchange_order_id,
                exchange_client_id,
                message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                _normalize_time_text(updated_at),
                symbol,
                side,
                order_type,
                state,
                _to_json(request_payload),
                _to_json(response_payload),
                _to_json(guardrails),
                exchange_order_id,
                exchange_client_id,
                message,
            ),
        )
        return int(cursor.lastrowid)


def update_execution_order(
    *,
    execution_order_id: int,
    updated_at: str,
    state: str,
    response_payload: dict[str, Any] | None,
    exchange_order_id: str | None,
    exchange_client_id: str | None,
    message: str,
):
    with _connect() as conn:
        conn.execute(
            """
            UPDATE execution_orders
            SET updated_at = ?,
                state = ?,
                response_json = ?,
                exchange_order_id = ?,
                exchange_client_id = ?,
                message = ?
            WHERE id = ?
            """,
            (
                _normalize_time_text(updated_at),
                state,
                _to_json(response_payload),
                exchange_order_id,
                exchange_client_id,
                message,
                execution_order_id,
            ),
        )


def insert_execution_order_event(
    *,
    execution_order_id: int,
    created_at: str,
    from_state: str,
    to_state: str,
    event_type: str,
    message: str,
    details: dict[str, Any] | None = None,
):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO execution_order_events (
                execution_order_id,
                created_at,
                from_state,
                to_state,
                event_type,
                message,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                execution_order_id,
                _normalize_time_text(created_at),
                from_state,
                to_state,
                event_type,
                message,
                _to_json(details),
            ),
        )


def insert_trade_journal(
    *,
    created_at: str,
    trading_mode: str,
    channel: str,
    status: str,
    symbol: str,
    side: str | None = None,
    signal_reason: str | None = None,
    exit_reason: str | None = None,
    request_rate: float | None = None,
    latest_price: float | None = None,
    amount_thb: float | None = None,
    amount_coin: float | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trade_journal (
                created_at,
                trading_mode,
                channel,
                status,
                symbol,
                side,
                signal_reason,
                exit_reason,
                request_rate,
                latest_price,
                amount_thb,
                amount_coin,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                str(trading_mode),
                str(channel),
                str(status),
                str(symbol),
                side,
                signal_reason,
                exit_reason,
                request_rate,
                latest_price,
                amount_thb,
                amount_coin,
                _to_json(details),
            ),
        )
        return int(cursor.lastrowid)


def insert_validation_run(
    *,
    created_at: str,
    validation_type: str,
    status: str,
    symbol: str,
    data_source: str,
    resolution: str | None,
    mode: str,
    date_from: str,
    date_to: str,
    train_window_days: int,
    test_window_days: int,
    step_days: int,
    fee_rate: float,
    cooldown_seconds: int,
    base_rule: dict[str, Any],
    summary: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO validation_runs (
                created_at,
                validation_type,
                status,
                symbol,
                data_source,
                resolution,
                mode,
                date_from,
                date_to,
                train_window_days,
                test_window_days,
                step_days,
                fee_rate,
                cooldown_seconds,
                base_rule_json,
                summary_json,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                str(validation_type),
                str(status),
                str(symbol),
                str(data_source),
                resolution,
                str(mode),
                str(date_from),
                str(date_to),
                int(train_window_days),
                int(test_window_days),
                int(step_days),
                float(fee_rate),
                int(cooldown_seconds),
                _to_json(base_rule),
                _to_json(summary),
                _to_json(metadata),
            ),
        )
        return int(cursor.lastrowid)


def update_validation_run(
    *,
    validation_run_id: int,
    status: str,
    summary: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE validation_runs
            SET status = ?,
                summary_json = ?,
                metadata_json = ?
            WHERE id = ?
            """,
            (
                str(status),
                _to_json(summary),
                _to_json(metadata),
                int(validation_run_id),
            ),
        )


def insert_validation_run_slice(
    *,
    validation_run_id: int,
    slice_no: int,
    status: str,
    train_start_at: str,
    train_end_at: str,
    test_start_at: str,
    test_end_at: str,
    selected_variant: str | None,
    selected_rule: dict[str, Any] | None,
    train_metrics: dict[str, Any] | None,
    test_metrics: dict[str, Any] | None,
    train_result_hash: str | None,
    test_result_hash: str | None,
    notes: list[str] | None = None,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO validation_run_slices (
                validation_run_id,
                slice_no,
                status,
                train_start_at,
                train_end_at,
                test_start_at,
                test_end_at,
                selected_variant,
                selected_rule_json,
                train_metrics_json,
                test_metrics_json,
                train_result_hash,
                test_result_hash,
                notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(validation_run_id),
                int(slice_no),
                str(status),
                _normalize_time_text(train_start_at),
                _normalize_time_text(train_end_at),
                _normalize_time_text(test_start_at),
                _normalize_time_text(test_end_at),
                selected_variant,
                _to_json(selected_rule),
                _to_json(train_metrics),
                _to_json(test_metrics),
                train_result_hash,
                test_result_hash,
                _to_json(notes or []),
            ),
        )
        return int(cursor.lastrowid)


def insert_validation_consistency_check(
    *,
    created_at: str,
    validation_run_id: int | None,
    check_type: str,
    status: str,
    symbol: str,
    data_source: str,
    resolution: str | None,
    window_start_at: str,
    window_end_at: str,
    rule: dict[str, Any],
    details: dict[str, Any] | None = None,
) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO validation_consistency_checks (
                created_at,
                validation_run_id,
                check_type,
                status,
                symbol,
                data_source,
                resolution,
                window_start_at,
                window_end_at,
                rule_json,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _normalize_time_text(created_at),
                int(validation_run_id) if validation_run_id is not None else None,
                str(check_type),
                str(status),
                str(symbol),
                str(data_source),
                resolution,
                _normalize_time_text(window_start_at),
                _normalize_time_text(window_end_at),
                _to_json(rule),
                _to_json(details),
            ),
        )
        return int(cursor.lastrowid)


def _prune_table_older_than(
    conn: sqlite3.Connection,
    *,
    table: str,
    timestamp_column: str,
    retention_days: int,
) -> int:
    cutoff_text = format_time_text(now_dt() - timedelta(days=retention_days))
    cursor = conn.execute(
        f"""
        DELETE FROM {table}
        WHERE {timestamp_column} < ?
        """,
        (cutoff_text,),
    )
    return int(cursor.rowcount or 0)


def prune_sqlite_retention(*, retention_days: dict[str, int]) -> dict[str, int]:
    with _connect() as conn:
        return {
            "market_snapshots": _prune_table_older_than(
                conn,
                table="market_snapshots",
                timestamp_column="created_at",
                retention_days=retention_days["market_snapshots"],
            ),
            "signal_logs": _prune_table_older_than(
                conn,
                table="signal_logs",
                timestamp_column="created_at",
                retention_days=retention_days["signal_logs"],
            ),
            "runtime_events": _prune_table_older_than(
                conn,
                table="runtime_events",
                timestamp_column="created_at",
                retention_days=retention_days["runtime_events"],
            ),
            "account_snapshots": _prune_table_older_than(
                conn,
                table="account_snapshots",
                timestamp_column="created_at",
                retention_days=retention_days["account_snapshots"],
            ),
            "reconciliation_results": _prune_table_older_than(
                conn,
                table="reconciliation_results",
                timestamp_column="created_at",
                retention_days=retention_days["reconciliation_results"],
            ),
            "trade_journal": _prune_table_older_than(
                conn,
                table="trade_journal",
                timestamp_column="created_at",
                retention_days=retention_days["runtime_events"],
            ),
            "validation_runs": _prune_table_older_than(
                conn,
                table="validation_runs",
                timestamp_column="created_at",
                retention_days=retention_days["runtime_events"],
            ),
            "validation_run_slices": _prune_table_older_than(
                conn,
                table="validation_run_slices",
                timestamp_column="test_end_at",
                retention_days=retention_days["runtime_events"],
            ),
            "validation_consistency_checks": _prune_table_older_than(
                conn,
                table="validation_consistency_checks",
                timestamp_column="created_at",
                retention_days=retention_days["runtime_events"],
            ),
        }


def _resolve_archive_dir(archive_dir_value: Any) -> Path:
    archive_dir_text = str(archive_dir_value or "").strip()
    archive_dir = Path(archive_dir_text or str(DEFAULT_ARCHIVE_DIR))
    if not archive_dir.is_absolute():
        archive_dir = PROJECT_ROOT / archive_dir
    return archive_dir


def _archive_file_path(
    *,
    archive_dir: Path,
    table_name: str,
    archive_date: str,
    archive_format: str,
    archive_compression: str,
) -> Path:
    year = archive_date[:4]
    month = archive_date[5:7]
    suffix = ".csv.gz" if archive_compression == RETENTION_ARCHIVE_COMPRESSION_GZIP else ".csv"
    if archive_format != RETENTION_ARCHIVE_FORMAT_CSV:
        raise ValueError(f"unsupported archive format: {archive_format}")
    return (
        archive_dir
        / table_name
        / year
        / month
        / f"{table_name}_{archive_date}{suffix}"
    )


def _csv_value(value: Any) -> Any:
    return "" if value is None else value


def _count_csv_archive_rows(path: Path, *, archive_compression: str) -> int:
    if not path.exists():
        return 0

    open_fn = gzip.open if archive_compression == RETENTION_ARCHIVE_COMPRESSION_GZIP else open
    with open_fn(path, "rt", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        row_count = -1
        for row_count, _ in enumerate(reader):
            pass
    return max(row_count, 0)


def _write_csv_archive_file(
    *,
    path: Path,
    rows: list[dict[str, Any]],
    fieldnames: list[str],
    archive_compression: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    open_fn = gzip.open if archive_compression == RETENTION_ARCHIVE_COMPRESSION_GZIP else open

    try:
        with open_fn(tmp_path, "wt", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: _csv_value(row.get(field)) for field in fieldnames})
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _serialize_archive_run(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None

    return {
        "id": int(row["id"]),
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "table_name": row["table_name"],
        "archive_date": row["archive_date"],
        "archive_start_at": row["archive_start_at"],
        "archive_end_at": row["archive_end_at"],
        "hot_retention_days": int(row["hot_retention_days"]),
        "archive_dir": row["archive_dir"],
        "archive_path": row["archive_path"],
        "archive_format": row["archive_format"],
        "archive_compression": row["archive_compression"],
        "record_count": int(row["record_count"] or 0),
        "archive_status": row["archive_status"],
        "cleanup_status": row["cleanup_status"],
        "cleanup_deleted_count": int(row["cleanup_deleted_count"] or 0),
        "cleanup_completed_at": row["cleanup_completed_at"],
        "error_message": row["error_message"],
    }


def _upsert_archive_run(
    conn: sqlite3.Connection,
    *,
    created_at: str,
    completed_at: str | None,
    table_name: str,
    archive_date: str,
    archive_start_at: str,
    archive_end_at: str,
    hot_retention_days: int,
    archive_dir: str,
    archive_path: str,
    archive_format: str,
    archive_compression: str,
    record_count: int,
    archive_status: str,
    cleanup_status: str,
    cleanup_deleted_count: int = 0,
    cleanup_completed_at: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO retention_archive_runs (
            created_at,
            completed_at,
            table_name,
            archive_date,
            archive_start_at,
            archive_end_at,
            hot_retention_days,
            archive_dir,
            archive_path,
            archive_format,
            archive_compression,
            record_count,
            archive_status,
            cleanup_status,
            cleanup_deleted_count,
            cleanup_completed_at,
            error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(table_name, archive_date) DO UPDATE SET
            created_at = COALESCE(retention_archive_runs.created_at, excluded.created_at),
            completed_at = excluded.completed_at,
            archive_start_at = excluded.archive_start_at,
            archive_end_at = excluded.archive_end_at,
            hot_retention_days = excluded.hot_retention_days,
            archive_dir = excluded.archive_dir,
            archive_path = excluded.archive_path,
            archive_format = excluded.archive_format,
            archive_compression = excluded.archive_compression,
            record_count = excluded.record_count,
            archive_status = excluded.archive_status,
            cleanup_status = excluded.cleanup_status,
            cleanup_deleted_count = excluded.cleanup_deleted_count,
            cleanup_completed_at = excluded.cleanup_completed_at,
            error_message = excluded.error_message
        """,
        (
            created_at,
            completed_at,
            table_name,
            archive_date,
            archive_start_at,
            archive_end_at,
            int(hot_retention_days),
            archive_dir,
            archive_path,
            archive_format,
            archive_compression,
            int(record_count),
            archive_status,
            cleanup_status,
            int(cleanup_deleted_count),
            cleanup_completed_at,
            error_message,
        ),
    )


def _fetch_retention_table_stats(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    timestamp_column: str,
) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS record_count,
            MIN({timestamp_column}) AS oldest_at,
            MAX({timestamp_column}) AS newest_at
        FROM {table_name}
        """,
    ).fetchone()
    return {
        "record_count": int(row["record_count"] or 0),
        "oldest_at": row["oldest_at"],
        "newest_at": row["newest_at"],
    }


def _archive_table_partition(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    timestamp_column: str,
    hot_retention_days: int,
    archive_enabled: bool,
    archive_dir: Path,
    archive_format: str,
    archive_compression: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    archive_dir_text = str(archive_dir)
    cutoff_date = format_date_text(now_dt() - timedelta(days=hot_retention_days))
    cutoff_start_at = f"{cutoff_date} 00:00:00"

    if not archive_enabled:
        return [
            {
                "table_name": table_name,
                "archive_date": None,
                "archive_status": "disabled",
                "cleanup_status": "not_required",
                "record_count": 0,
                "cleanup_deleted_count": 0,
                "archive_path": None,
                "error_message": None,
            }
        ]

    candidate_dates = [
        str(row["archive_date"])
        for row in conn.execute(
            f"""
            SELECT DISTINCT substr({timestamp_column}, 1, 10) AS archive_date
            FROM {table_name}
            WHERE {timestamp_column} < ?
            ORDER BY archive_date ASC
            """,
            (cutoff_start_at,),
        ).fetchall()
        if str(row["archive_date"] or "") < cutoff_date
    ]

    for archive_date in candidate_dates:
        archive_path = _archive_file_path(
            archive_dir=archive_dir,
            table_name=table_name,
            archive_date=archive_date,
            archive_format=archive_format,
            archive_compression=archive_compression,
        )
        existing_run = conn.execute(
            """
            SELECT *
            FROM retention_archive_runs
            WHERE table_name = ? AND archive_date = ?
            """,
            (table_name, archive_date),
        ).fetchone()

        day_start_at, day_end_at = _day_range_bounds(archive_date)
        rows = [dict(row) for row in conn.execute(
            f"""
            SELECT *
            FROM {table_name}
            WHERE {timestamp_column} >= ? AND {timestamp_column} < ?
            ORDER BY id ASC
            """,
            (day_start_at, day_end_at),
        ).fetchall()]

        if not rows and archive_path.exists():
            record_count = (
                int(existing_run["record_count"])
                if existing_run is not None
                else _count_csv_archive_rows(
                    archive_path,
                    archive_compression=archive_compression,
                )
            )
            archive_start_at = (
                existing_run["archive_start_at"]
                if existing_run is not None
                else day_start_at
            )
            archive_end_at = (
                existing_run["archive_end_at"]
                if existing_run is not None
                else day_end_at
            )
            with conn:
                _upsert_archive_run(
                    conn,
                    created_at=(
                        existing_run["created_at"]
                        if existing_run is not None
                        else format_time_text(now_dt())
                    ),
                    completed_at=(
                        existing_run["completed_at"]
                        if existing_run is not None
                        else format_time_text(now_dt())
                    ),
                    table_name=table_name,
                    archive_date=archive_date,
                    archive_start_at=archive_start_at,
                    archive_end_at=archive_end_at,
                    hot_retention_days=hot_retention_days,
                    archive_dir=archive_dir_text,
                    archive_path=str(archive_path),
                    archive_format=archive_format,
                    archive_compression=archive_compression,
                    record_count=record_count,
                    archive_status="archived",
                    cleanup_status=(
                        existing_run["cleanup_status"]
                        if existing_run is not None
                        else "pending"
                    ),
                    cleanup_deleted_count=(
                        int(existing_run["cleanup_deleted_count"] or 0)
                        if existing_run is not None
                        else 0
                    ),
                    cleanup_completed_at=(
                        existing_run["cleanup_completed_at"]
                        if existing_run is not None
                        else None
                    ),
                    error_message=None,
                )
            results.append(
                {
                    "table_name": table_name,
                    "archive_date": archive_date,
                    "archive_status": "archived",
                    "cleanup_status": (
                        existing_run["cleanup_status"]
                        if existing_run is not None
                        else "pending"
                    ),
                    "record_count": record_count,
                    "cleanup_deleted_count": (
                        int(existing_run["cleanup_deleted_count"] or 0)
                        if existing_run is not None
                        else 0
                    ),
                    "archive_path": str(archive_path),
                    "error_message": None,
                }
            )
            continue

        if not rows:
            results.append(
                {
                    "table_name": table_name,
                    "archive_date": archive_date,
                    "archive_status": "skipped",
                    "cleanup_status": "not_required",
                    "record_count": 0,
                    "cleanup_deleted_count": 0,
                    "archive_path": str(archive_path),
                    "error_message": None,
                }
            )
            continue

        if not archive_path.exists():
            _write_csv_archive_file(
                path=archive_path,
                rows=rows,
                fieldnames=list(rows[0].keys()),
                archive_compression=archive_compression,
            )

        archive_start_at = str(rows[0][timestamp_column] or day_start_at)
        archive_end_at = str(rows[-1][timestamp_column] or day_end_at)
        record_count = len(rows)

        with conn:
            _upsert_archive_run(
                conn,
                created_at=(
                    existing_run["created_at"]
                    if existing_run is not None
                    else format_time_text(now_dt())
                ),
                completed_at=format_time_text(now_dt()),
                table_name=table_name,
                archive_date=archive_date,
                archive_start_at=archive_start_at,
                archive_end_at=archive_end_at,
                hot_retention_days=hot_retention_days,
                archive_dir=archive_dir_text,
                archive_path=str(archive_path),
                archive_format=archive_format,
                archive_compression=archive_compression,
                record_count=record_count,
                archive_status="archived",
                cleanup_status=(
                    existing_run["cleanup_status"]
                    if existing_run is not None
                    else "pending"
                ),
                cleanup_deleted_count=(
                    int(existing_run["cleanup_deleted_count"] or 0)
                    if existing_run is not None
                    else 0
                ),
                cleanup_completed_at=(
                    existing_run["cleanup_completed_at"]
                    if existing_run is not None
                    else None
                ),
                error_message=None,
            )
        results.append(
            {
                "table_name": table_name,
                "archive_date": archive_date,
                "archive_status": "archived",
                "cleanup_status": (
                    existing_run["cleanup_status"]
                    if existing_run is not None
                    else "pending"
                ),
                "record_count": record_count,
                "cleanup_deleted_count": (
                    int(existing_run["cleanup_deleted_count"] or 0)
                    if existing_run is not None
                    else 0
                ),
                "archive_path": str(archive_path),
                "error_message": None,
            }
        )

    return results


def archive_sqlite_retention(*, config: dict[str, Any]) -> dict[str, Any]:
    archive_enabled = bool(config.get("archive_enabled", True))
    archive_dir = _resolve_archive_dir(config.get("archive_dir"))
    archive_format = str(config.get("archive_format", RETENTION_ARCHIVE_FORMAT_CSV)).lower()
    archive_compression = str(
        config.get("archive_compression", RETENTION_ARCHIVE_COMPRESSION_GZIP)
    ).lower()

    errors: list[str] = []
    if archive_format != RETENTION_ARCHIVE_FORMAT_CSV:
        errors.append(f"unsupported archive format: {archive_format}")
    if archive_compression not in {"none", RETENTION_ARCHIVE_COMPRESSION_GZIP}:
        errors.append(f"unsupported archive compression: {archive_compression}")

    table_results: dict[str, list[dict[str, Any]]] = {}
    archived_total = 0
    with _connect() as conn:
        for table_name, spec in RETENTION_ARCHIVE_TABLES.items():
            table_enabled = bool(config.get(spec["archive_enabled_key"], True))
            hot_retention_days = int(config.get(spec["hot_retention_key"], 90))
            if errors or not archive_enabled or not table_enabled:
                table_results[table_name] = [
                    {
                        "table_name": table_name,
                        "archive_date": None,
                        "archive_status": "disabled" if not archive_enabled or not table_enabled else "error",
                        "cleanup_status": "not_required",
                        "record_count": 0,
                        "cleanup_deleted_count": 0,
                        "archive_path": None,
                        "error_message": "; ".join(errors) if errors else None,
                    }
                ]
                continue

            results = _archive_table_partition(
                conn,
                table_name=table_name,
                timestamp_column=spec["timestamp_column"],
                hot_retention_days=hot_retention_days,
                archive_enabled=True,
                archive_dir=archive_dir,
                archive_format=archive_format,
                archive_compression=archive_compression,
            )
            table_results[table_name] = results
            archived_total += sum(
                int(result.get("record_count", 0) or 0)
                for result in results
                if result.get("archive_status") == "archived"
            )

    return {
        "archive_enabled": archive_enabled,
        "archive_dir": str(archive_dir),
        "archive_format": archive_format,
        "archive_compression": archive_compression,
        "archived_total": archived_total,
        "tables": table_results,
        "errors": errors,
    }


def _delete_archived_table_partitions(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    timestamp_column: str,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    archive_runs = conn.execute(
        """
        SELECT *
        FROM retention_archive_runs
        WHERE table_name = ?
          AND archive_status = 'archived'
          AND cleanup_status != 'deleted'
        ORDER BY archive_date ASC, id ASC
        """,
        (table_name,),
    ).fetchall()

    for run in archive_runs:
        archive_date = str(run["archive_date"])
        day_start_at, day_end_at = _day_range_bounds(archive_date)
        with conn:
            cursor = conn.execute(
                f"""
                DELETE FROM {table_name}
                WHERE {timestamp_column} >= ? AND {timestamp_column} < ?
                """,
                (day_start_at, day_end_at),
            )
            deleted_count = int(cursor.rowcount or 0)
            _upsert_archive_run(
                conn,
                created_at=str(run["created_at"]),
                completed_at=str(run["completed_at"] or format_time_text(now_dt())),
                table_name=table_name,
                archive_date=archive_date,
                archive_start_at=str(run["archive_start_at"]),
                archive_end_at=str(run["archive_end_at"]),
                hot_retention_days=int(run["hot_retention_days"]),
                archive_dir=str(run["archive_dir"]),
                archive_path=str(run["archive_path"]),
                archive_format=str(run["archive_format"]),
                archive_compression=str(run["archive_compression"]),
                record_count=int(run["record_count"] or 0),
                archive_status=str(run["archive_status"]),
                cleanup_status="deleted",
                cleanup_deleted_count=deleted_count,
                cleanup_completed_at=format_time_text(now_dt()),
                error_message=None,
            )
        results.append(
            {
                "table_name": table_name,
                "archive_date": archive_date,
                "deleted_count": deleted_count,
                "archive_path": str(run["archive_path"]),
            }
        )

    return results


def cleanup_sqlite_retention(*, config: dict[str, Any]) -> dict[str, Any]:
    archive_summary = archive_sqlite_retention(config=config)
    deleted_rows: dict[str, int] = {}
    table_results: dict[str, list[dict[str, Any]]] = {}
    with _connect() as conn:
        for table_name, spec in RETENTION_ARCHIVE_TABLES.items():
            table_delete_results = _delete_archived_table_partitions(
                conn,
                table_name=table_name,
                timestamp_column=spec["timestamp_column"],
            )
            table_results[table_name] = table_delete_results
            deleted_rows[table_name] = sum(
                int(result.get("deleted_count", 0) or 0)
                for result in table_delete_results
            )
        for table_name, spec in RETENTION_RUNTIME_TABLES.items():
            deleted_rows[table_name] = _prune_table_older_than(
                conn,
                table=table_name,
                timestamp_column=spec["timestamp_column"],
                retention_days=int(config.get(spec["retention_key"], 30)),
            )

    return {
        "archive": archive_summary,
        "deleted_rows": deleted_rows,
        "archived_total": int(archive_summary.get("archived_total", 0) or 0),
        "deleted_total": sum(deleted_rows.values()),
        "archive_delete_results": table_results,
    }


def fetch_retention_status_summary() -> dict[str, Any]:
    table_names = ("market_snapshots", "signal_logs", "account_snapshots", "reconciliation_results", "runtime_events")
    timestamp_columns = {
        "market_snapshots": "created_at",
        "signal_logs": "created_at",
        "account_snapshots": "created_at",
        "reconciliation_results": "created_at",
        "runtime_events": "created_at",
    }

    with _connect() as conn:
        tables: list[dict[str, Any]] = []
        for table_name in table_names:
            stats = _fetch_retention_table_stats(
                conn,
                table_name=table_name,
                timestamp_column=timestamp_columns[table_name],
            )
            latest_archive_row = conn.execute(
                """
                SELECT *
                FROM retention_archive_runs
                WHERE table_name = ?
                ORDER BY archive_date DESC, id DESC
                LIMIT 1
                """,
                (table_name,),
            ).fetchone()
            latest_archive = _serialize_archive_run(latest_archive_row)
            tables.append(
                {
                    "table_name": table_name,
                    "record_count": stats["record_count"],
                    "oldest_at": stats["oldest_at"],
                    "newest_at": stats["newest_at"],
                    "latest_archive_run": latest_archive,
                }
            )

        latest_archive_overall = conn.execute(
            """
            SELECT *
            FROM retention_archive_runs
            WHERE archive_status = 'archived'
            ORDER BY completed_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_cleanup = conn.execute(
            """
            SELECT created_at, message, details_json
            FROM runtime_events
            WHERE event_type = 'sqlite_retention_cleanup'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    return {
        "tables": tables,
        "latest_archive_run": _serialize_archive_run(latest_archive_overall),
        "latest_cleanup": (
            {
                "created_at": latest_cleanup["created_at"],
                "message": latest_cleanup["message"],
                "details": _load_json(latest_cleanup["details_json"], {}),
            }
            if latest_cleanup
            else None
        ),
    }


def _load_json(value: str | None, default: Any):
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if float(denominator) else 0.0


def _empty_strategy_metric_row(
    *, report_date: str, strategy_key: str, trading_mode: str, symbol: str
) -> dict[str, Any]:
    return {
        "report_date": report_date,
        "strategy_key": strategy_key,
        "trading_mode": trading_mode,
        "symbol": symbol,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl_thb": 0.0,
        "fee_thb": 0.0,
        "turnover_thb": 0.0,
        "gross_win_thb": 0.0,
        "gross_loss_thb": 0.0,
        "gross_pnl_before_fees_thb": 0.0,
        "avg_pnl_thb": 0.0,
        "avg_fee_thb": 0.0,
        "win_rate_percent": 0.0,
        "profit_factor": 0.0,
    }


def _finalize_strategy_metric_row(row: dict[str, Any]) -> dict[str, Any]:
    closed_trades = int(row.get("closed_trades", 0) or 0)
    wins = int(row.get("wins", 0) or 0)
    gross_win_thb = float(row.get("gross_win_thb", 0.0) or 0.0)
    gross_loss_thb = float(row.get("gross_loss_thb", 0.0) or 0.0)
    realized_pnl_thb = float(row.get("realized_pnl_thb", 0.0) or 0.0)
    fee_thb = float(row.get("fee_thb", 0.0) or 0.0)

    row["avg_pnl_thb"] = _safe_div(realized_pnl_thb, closed_trades)
    row["avg_fee_thb"] = _safe_div(fee_thb, closed_trades)
    row["win_rate_percent"] = _safe_div(wins * 100.0, closed_trades)
    row["profit_factor"] = _safe_div(gross_win_thb, gross_loss_thb)
    return row


def refresh_daily_performance_metrics_from_history(
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, int]:
    if conn is None:
        with _connect() as local_conn:
            return refresh_daily_performance_metrics_from_history(conn=local_conn)

    paper_rows = conn.execute(
        """
        SELECT
            buy_time,
            sell_time,
            symbol,
            budget_thb,
            gross_proceeds_thb,
            buy_fee_thb,
            sell_fee_thb,
            pnl_thb
        FROM paper_trade_logs
        ORDER BY sell_time ASC, id ASC
        """
    ).fetchall()
    filled_orders, closed_trades = _build_live_execution_trade_history(conn=conn)

    strategy_daily: dict[tuple[str, str, str], dict[str, Any]] = {}

    def ensure_strategy_row(
        *, report_date: str, strategy_key: str, trading_mode: str, symbol: str
    ) -> dict[str, Any]:
        key = (report_date, strategy_key, symbol)
        return strategy_daily.setdefault(
            key,
            _empty_strategy_metric_row(
                report_date=report_date,
                strategy_key=strategy_key,
                trading_mode=trading_mode,
                symbol=symbol,
            ),
        )

    for row in paper_rows:
        report_date = str(row["sell_time"] or "")[:10]
        if len(report_date) != 10:
            continue
        pnl_thb = float(row["pnl_thb"] or 0.0)
        fee_thb = float(row["buy_fee_thb"] or 0.0) + float(row["sell_fee_thb"] or 0.0)
        turnover_thb = float(row["budget_thb"] or 0.0) + float(
            row["gross_proceeds_thb"] or 0.0
        )
        metric_row = ensure_strategy_row(
            report_date=report_date,
            strategy_key="paper_rule_engine",
            trading_mode="paper",
            symbol=str(row["symbol"]),
        )
        metric_row["closed_trades"] += 1
        metric_row["wins"] += 1 if pnl_thb > 0 else 0
        metric_row["losses"] += 0 if pnl_thb > 0 else 1
        metric_row["realized_pnl_thb"] += pnl_thb
        metric_row["fee_thb"] += fee_thb
        metric_row["turnover_thb"] += turnover_thb
        metric_row["gross_win_thb"] += max(pnl_thb, 0.0)
        metric_row["gross_loss_thb"] += abs(min(pnl_thb, 0.0))
        metric_row["gross_pnl_before_fees_thb"] += pnl_thb + fee_thb

    for row in closed_trades:
        report_date = str(row.get("updated_at") or row.get("created_at") or "")[:10]
        if len(report_date) != 10:
            continue
        pnl_thb = float(row.get("pnl_thb", 0.0) or 0.0)
        fee_thb = float(row.get("fee_thb", 0.0) or 0.0)
        turnover_thb = float(row.get("cost_basis_thb", 0.0) or 0.0) + float(
            row.get("gross_proceeds_thb", 0.0) or 0.0
        )
        metric_row = ensure_strategy_row(
            report_date=report_date,
            strategy_key="live_execution",
            trading_mode="live",
            symbol=str(row["symbol"]),
        )
        metric_row["closed_trades"] += 1
        metric_row["wins"] += 1 if pnl_thb > 0 else 0
        metric_row["losses"] += 0 if pnl_thb > 0 else 1
        metric_row["realized_pnl_thb"] += pnl_thb
        metric_row["fee_thb"] += fee_thb
        metric_row["turnover_thb"] += turnover_thb
        metric_row["gross_win_thb"] += max(pnl_thb, 0.0)
        metric_row["gross_loss_thb"] += abs(min(pnl_thb, 0.0))
        metric_row["gross_pnl_before_fees_thb"] += float(
            row.get("gross_pnl_before_fees_thb", 0.0) or 0.0
        )

    strategy_rows = [
        _finalize_strategy_metric_row(row)
        for _, row in sorted(strategy_daily.items(), key=lambda item: item[0])
    ]

    portfolio_daily: dict[str, dict[str, Any]] = {}
    ordered_symbols_by_day: dict[str, set[str]] = {}
    ordered_strategies_by_day: dict[str, set[str]] = {}
    for row in strategy_rows:
        report_date = str(row["report_date"])
        strategy_key = str(row["strategy_key"])
        bucket = portfolio_daily.setdefault(
            report_date,
            {
                "report_date": report_date,
                "strategies_active": 0,
                "symbols_active": 0,
                "paper_closed_trades": 0,
                "live_closed_trades": 0,
                "combined_closed_trades": 0,
                "paper_wins": 0,
                "paper_losses": 0,
                "live_wins": 0,
                "live_losses": 0,
                "combined_wins": 0,
                "combined_losses": 0,
                "paper_realized_pnl_thb": 0.0,
                "live_realized_pnl_thb": 0.0,
                "combined_realized_pnl_thb": 0.0,
                "paper_fee_thb": 0.0,
                "live_fee_thb": 0.0,
                "combined_fee_thb": 0.0,
                "paper_turnover_thb": 0.0,
                "live_turnover_thb": 0.0,
                "combined_turnover_thb": 0.0,
                "paper_gross_win_thb": 0.0,
                "paper_gross_loss_thb": 0.0,
                "live_gross_win_thb": 0.0,
                "live_gross_loss_thb": 0.0,
                "combined_gross_win_thb": 0.0,
                "combined_gross_loss_thb": 0.0,
                "paper_gross_pnl_before_fees_thb": 0.0,
                "live_gross_pnl_before_fees_thb": 0.0,
                "combined_gross_pnl_before_fees_thb": 0.0,
                "paper_win_rate_percent": 0.0,
                "live_win_rate_percent": 0.0,
                "combined_win_rate_percent": 0.0,
                "paper_profit_factor": 0.0,
                "live_profit_factor": 0.0,
                "combined_profit_factor": 0.0,
                "cumulative_realized_pnl_thb": 0.0,
                "peak_cumulative_realized_pnl_thb": 0.0,
                "drawdown_thb": 0.0,
            },
        )
        ordered_symbols_by_day.setdefault(report_date, set()).add(str(row["symbol"]))
        ordered_strategies_by_day.setdefault(report_date, set()).add(strategy_key)

        prefix = "paper" if strategy_key == "paper_rule_engine" else "live"
        bucket[f"{prefix}_closed_trades"] += int(row["closed_trades"])
        bucket[f"{prefix}_wins"] += int(row["wins"])
        bucket[f"{prefix}_losses"] += int(row["losses"])
        bucket[f"{prefix}_realized_pnl_thb"] += float(row["realized_pnl_thb"])
        bucket[f"{prefix}_fee_thb"] += float(row["fee_thb"])
        bucket[f"{prefix}_turnover_thb"] += float(row["turnover_thb"])
        bucket[f"{prefix}_gross_win_thb"] += float(row["gross_win_thb"])
        bucket[f"{prefix}_gross_loss_thb"] += float(row["gross_loss_thb"])
        bucket[f"{prefix}_gross_pnl_before_fees_thb"] += float(
            row["gross_pnl_before_fees_thb"]
        )

    cumulative_realized_pnl_thb = 0.0
    peak_cumulative_realized_pnl_thb = 0.0
    portfolio_rows: list[dict[str, Any]] = []
    for report_date in sorted(portfolio_daily):
        row = portfolio_daily[report_date]
        row["strategies_active"] = len(ordered_strategies_by_day.get(report_date, set()))
        row["symbols_active"] = len(ordered_symbols_by_day.get(report_date, set()))
        row["combined_closed_trades"] = int(row["paper_closed_trades"]) + int(
            row["live_closed_trades"]
        )
        row["combined_wins"] = int(row["paper_wins"]) + int(row["live_wins"])
        row["combined_losses"] = int(row["paper_losses"]) + int(row["live_losses"])
        row["combined_realized_pnl_thb"] = float(row["paper_realized_pnl_thb"]) + float(
            row["live_realized_pnl_thb"]
        )
        row["combined_fee_thb"] = float(row["paper_fee_thb"]) + float(
            row["live_fee_thb"]
        )
        row["combined_turnover_thb"] = float(row["paper_turnover_thb"]) + float(
            row["live_turnover_thb"]
        )
        row["combined_gross_win_thb"] = float(row["paper_gross_win_thb"]) + float(
            row["live_gross_win_thb"]
        )
        row["combined_gross_loss_thb"] = float(row["paper_gross_loss_thb"]) + float(
            row["live_gross_loss_thb"]
        )
        row["combined_gross_pnl_before_fees_thb"] = float(
            row["paper_gross_pnl_before_fees_thb"]
        ) + float(row["live_gross_pnl_before_fees_thb"])
        row["paper_win_rate_percent"] = _safe_div(
            float(row["paper_wins"]) * 100.0, float(row["paper_closed_trades"])
        )
        row["live_win_rate_percent"] = _safe_div(
            float(row["live_wins"]) * 100.0, float(row["live_closed_trades"])
        )
        row["combined_win_rate_percent"] = _safe_div(
            float(row["combined_wins"]) * 100.0, float(row["combined_closed_trades"])
        )
        row["paper_profit_factor"] = _safe_div(
            float(row["paper_gross_win_thb"]), float(row["paper_gross_loss_thb"])
        )
        row["live_profit_factor"] = _safe_div(
            float(row["live_gross_win_thb"]), float(row["live_gross_loss_thb"])
        )
        row["combined_profit_factor"] = _safe_div(
            float(row["combined_gross_win_thb"]), float(row["combined_gross_loss_thb"])
        )
        cumulative_realized_pnl_thb += float(row["combined_realized_pnl_thb"])
        peak_cumulative_realized_pnl_thb = max(
            peak_cumulative_realized_pnl_thb, cumulative_realized_pnl_thb
        )
        row["cumulative_realized_pnl_thb"] = cumulative_realized_pnl_thb
        row["peak_cumulative_realized_pnl_thb"] = peak_cumulative_realized_pnl_thb
        row["drawdown_thb"] = cumulative_realized_pnl_thb - peak_cumulative_realized_pnl_thb
        portfolio_rows.append(row)

    conn.execute("DELETE FROM strategy_daily_metrics")
    conn.execute("DELETE FROM portfolio_daily_metrics")

    if strategy_rows:
        conn.executemany(
            """
            INSERT INTO strategy_daily_metrics (
                report_date,
                strategy_key,
                trading_mode,
                symbol,
                closed_trades,
                wins,
                losses,
                realized_pnl_thb,
                fee_thb,
                turnover_thb,
                gross_win_thb,
                gross_loss_thb,
                gross_pnl_before_fees_thb,
                avg_pnl_thb,
                avg_fee_thb,
                win_rate_percent,
                profit_factor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["report_date"],
                    row["strategy_key"],
                    row["trading_mode"],
                    row["symbol"],
                    int(row["closed_trades"]),
                    int(row["wins"]),
                    int(row["losses"]),
                    float(row["realized_pnl_thb"]),
                    float(row["fee_thb"]),
                    float(row["turnover_thb"]),
                    float(row["gross_win_thb"]),
                    float(row["gross_loss_thb"]),
                    float(row["gross_pnl_before_fees_thb"]),
                    float(row["avg_pnl_thb"]),
                    float(row["avg_fee_thb"]),
                    float(row["win_rate_percent"]),
                    float(row["profit_factor"]),
                )
                for row in strategy_rows
            ],
        )

    if portfolio_rows:
        conn.executemany(
            """
            INSERT INTO portfolio_daily_metrics (
                report_date,
                strategies_active,
                symbols_active,
                paper_closed_trades,
                live_closed_trades,
                combined_closed_trades,
                paper_wins,
                paper_losses,
                live_wins,
                live_losses,
                combined_wins,
                combined_losses,
                paper_realized_pnl_thb,
                live_realized_pnl_thb,
                combined_realized_pnl_thb,
                paper_fee_thb,
                live_fee_thb,
                combined_fee_thb,
                paper_turnover_thb,
                live_turnover_thb,
                combined_turnover_thb,
                paper_gross_win_thb,
                paper_gross_loss_thb,
                live_gross_win_thb,
                live_gross_loss_thb,
                combined_gross_win_thb,
                combined_gross_loss_thb,
                paper_gross_pnl_before_fees_thb,
                live_gross_pnl_before_fees_thb,
                combined_gross_pnl_before_fees_thb,
                paper_win_rate_percent,
                live_win_rate_percent,
                combined_win_rate_percent,
                paper_profit_factor,
                live_profit_factor,
                combined_profit_factor,
                cumulative_realized_pnl_thb,
                peak_cumulative_realized_pnl_thb,
                drawdown_thb
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["report_date"],
                    int(row["strategies_active"]),
                    int(row["symbols_active"]),
                    int(row["paper_closed_trades"]),
                    int(row["live_closed_trades"]),
                    int(row["combined_closed_trades"]),
                    int(row["paper_wins"]),
                    int(row["paper_losses"]),
                    int(row["live_wins"]),
                    int(row["live_losses"]),
                    int(row["combined_wins"]),
                    int(row["combined_losses"]),
                    float(row["paper_realized_pnl_thb"]),
                    float(row["live_realized_pnl_thb"]),
                    float(row["combined_realized_pnl_thb"]),
                    float(row["paper_fee_thb"]),
                    float(row["live_fee_thb"]),
                    float(row["combined_fee_thb"]),
                    float(row["paper_turnover_thb"]),
                    float(row["live_turnover_thb"]),
                    float(row["combined_turnover_thb"]),
                    float(row["paper_gross_win_thb"]),
                    float(row["paper_gross_loss_thb"]),
                    float(row["live_gross_win_thb"]),
                    float(row["live_gross_loss_thb"]),
                    float(row["combined_gross_win_thb"]),
                    float(row["combined_gross_loss_thb"]),
                    float(row["paper_gross_pnl_before_fees_thb"]),
                    float(row["live_gross_pnl_before_fees_thb"]),
                    float(row["combined_gross_pnl_before_fees_thb"]),
                    float(row["paper_win_rate_percent"]),
                    float(row["live_win_rate_percent"]),
                    float(row["combined_win_rate_percent"]),
                    float(row["paper_profit_factor"]),
                    float(row["live_profit_factor"]),
                    float(row["combined_profit_factor"]),
                    float(row["cumulative_realized_pnl_thb"]),
                    float(row["peak_cumulative_realized_pnl_thb"]),
                    float(row["drawdown_thb"]),
                )
                for row in portfolio_rows
            ],
        )

    return {
        "strategy_daily_metrics": len(strategy_rows),
        "portfolio_daily_metrics": len(portfolio_rows),
    }


def _fetch_portfolio_daily_metrics_from_conn(
    *,
    conn: sqlite3.Connection,
    days: int,
) -> list[dict[str, Any]]:
    cutoff_day = format_date_text(now_dt() - timedelta(days=max(1, int(days)) - 1))
    rows = conn.execute(
        """
        SELECT *
        FROM portfolio_daily_metrics
        WHERE report_date >= ?
        ORDER BY report_date DESC
        """,
        (cutoff_day,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_portfolio_daily_metrics(*, days: int = 30) -> list[dict[str, Any]]:
    with _connect() as conn:
        refresh_daily_performance_metrics_from_history(conn=conn)
        return _fetch_portfolio_daily_metrics_from_conn(conn=conn, days=days)


def _fetch_strategy_daily_metrics_from_conn(
    *,
    conn: sqlite3.Connection,
    days: int,
    symbol: str | None = None,
    strategy_key: str | None = None,
) -> list[dict[str, Any]]:
    cutoff_day = format_date_text(now_dt() - timedelta(days=max(1, int(days)) - 1))
    clauses = ["report_date >= ?"]
    params: list[Any] = [cutoff_day]
    if symbol:
        clauses.append("symbol = ?")
        params.append(str(symbol))
    if strategy_key:
        clauses.append("strategy_key = ?")
        params.append(str(strategy_key))
    rows = conn.execute(
        f"""
        SELECT *
        FROM strategy_daily_metrics
        WHERE {' AND '.join(clauses)}
        ORDER BY report_date DESC, strategy_key ASC, symbol ASC
        """,
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_strategy_daily_metrics(
    *,
    days: int = 30,
    symbol: str | None = None,
    strategy_key: str | None = None,
) -> list[dict[str, Any]]:
    with _connect() as conn:
        refresh_daily_performance_metrics_from_history(conn=conn)
        return _fetch_strategy_daily_metrics_from_conn(
            conn=conn,
            days=days,
            symbol=symbol,
            strategy_key=strategy_key,
        )


def fetch_db_maintenance_summary() -> dict[str, Any]:
    table_names = (
        "runtime_events",
        "signal_logs",
        "market_snapshots",
        "market_candles",
        "paper_trade_logs",
        "account_snapshots",
        "reconciliation_results",
        "execution_orders",
        "execution_order_events",
        "trade_journal",
        "strategy_daily_metrics",
        "portfolio_daily_metrics",
        "validation_runs",
        "validation_run_slices",
        "validation_consistency_checks",
        "telegram_outbox",
        "telegram_command_log",
    )

    with _connect() as conn:
        table_counts = {
            table_name: int(
                conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"]
            )
            for table_name in table_names
        }
        latest_cleanup = conn.execute(
            """
            SELECT created_at, message, details_json
            FROM runtime_events
            WHERE event_type = 'sqlite_retention_cleanup'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    db_exists = DB_PATH.exists()
    db_size_bytes = DB_PATH.stat().st_size if db_exists else 0

    return {
        "db_exists": db_exists,
        "db_size_bytes": int(db_size_bytes),
        "table_counts": table_counts,
        "latest_cleanup": (
            {
                "created_at": latest_cleanup["created_at"],
                "message": latest_cleanup["message"],
                "details": _load_json(latest_cleanup["details_json"], {}),
            }
            if latest_cleanup
            else None
        ),
        "retention_status": fetch_retention_status_summary(),
    }


def _extract_execution_fill_rate(response_payload: dict[str, Any], request_payload: dict[str, Any]) -> float:
    result = response_payload.get("result") if isinstance(response_payload, dict) else None
    history = result.get("history") if isinstance(result, dict) else None
    if isinstance(history, list) and history:
        try:
            return float(history[-1].get("rate") or 0.0)
        except (TypeError, ValueError):
            pass
    if isinstance(result, dict):
        try:
            rate = float(result.get("rate") or 0.0)
            if rate > 0:
                return rate
        except (TypeError, ValueError):
            pass
    try:
        return float(request_payload.get("rat") or 0.0)
    except (TypeError, ValueError):
        return 0.0



def _extract_execution_fee_thb(response_payload: dict[str, Any]) -> float:
    result = response_payload.get("result") if isinstance(response_payload, dict) else None
    if not isinstance(result, dict):
        return 0.0
    try:
        return float(result.get("fee") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_filled_execution_order_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, created_at, updated_at, symbol, side, request_json, response_json
        FROM execution_orders
        WHERE state = 'filled'
        ORDER BY created_at ASC, id ASC
        """
    ).fetchall()


def _build_live_execution_trade_history_from_rows(
    rows: list[sqlite3.Row],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    inventory: dict[str, list[dict[str, float]]] = {}
    closed_trades: list[dict[str, Any]] = []
    filled_orders: list[dict[str, Any]] = []

    for row in rows:
        symbol = str(row["symbol"])
        side = str(row["side"])
        request_payload = _load_json(row["request_json"], {})
        response_payload = _load_json(row["response_json"], {})
        fill_rate = _extract_execution_fill_rate(response_payload, request_payload)
        fee_thb = _extract_execution_fee_thb(response_payload)
        event_time = str(row["updated_at"] or row["created_at"] or "")
        filled_orders.append(
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "symbol": symbol,
                "side": side,
                "fee_thb": fee_thb,
                "event_time": event_time,
            }
        )
        lots = inventory.setdefault(symbol, [])

        if side == "buy":
            try:
                amount_thb = float(request_payload.get("amt") or 0.0)
            except (TypeError, ValueError):
                amount_thb = 0.0
            if fill_rate > 0 and amount_thb > 0:
                net_budget_thb = max(0.0, amount_thb - fee_thb)
                net_coin_qty = max(0.0, net_budget_thb / fill_rate)
                if net_coin_qty > 0:
                    lots.append(
                        {
                            "coin_qty": net_coin_qty,
                            "cost_thb": amount_thb,
                            "buy_rate": fill_rate,
                            "buy_fee_thb": fee_thb,
                            "net_budget_thb": net_budget_thb,
                        }
                    )
            continue

        if side != "sell":
            continue

        try:
            sell_coin_qty = float(request_payload.get("amt") or 0.0)
        except (TypeError, ValueError):
            sell_coin_qty = 0.0
        if fill_rate <= 0 or sell_coin_qty <= 0:
            continue

        remaining_qty = sell_coin_qty
        cost_basis_thb = 0.0
        buy_fee_allocated_thb = 0.0
        net_budget_allocated_thb = 0.0
        while remaining_qty > 1e-12 and lots:
            current_lot = lots[0]
            lot_qty = float(current_lot.get("coin_qty") or 0.0)
            lot_cost = float(current_lot.get("cost_thb") or 0.0)
            lot_buy_fee = float(current_lot.get("buy_fee_thb") or 0.0)
            lot_net_budget = float(current_lot.get("net_budget_thb") or 0.0)
            if lot_qty <= 1e-12:
                lots.pop(0)
                continue
            consume_qty = min(remaining_qty, lot_qty)
            consume_ratio = consume_qty / lot_qty if lot_qty > 0 else 0.0
            cost_basis_thb += lot_cost * consume_ratio
            buy_fee_allocated_thb += lot_buy_fee * consume_ratio
            net_budget_allocated_thb += lot_net_budget * consume_ratio
            current_lot["coin_qty"] = max(0.0, lot_qty - consume_qty)
            current_lot["cost_thb"] = max(0.0, lot_cost - (lot_cost * consume_ratio))
            current_lot["buy_fee_thb"] = max(
                0.0, lot_buy_fee - (lot_buy_fee * consume_ratio)
            )
            current_lot["net_budget_thb"] = max(
                0.0, lot_net_budget - (lot_net_budget * consume_ratio)
            )
            remaining_qty -= consume_qty
            if current_lot["coin_qty"] <= 1e-12:
                lots.pop(0)

        matched_qty = sell_coin_qty - remaining_qty
        if matched_qty <= 1e-12:
            continue

        gross_proceeds_thb = matched_qty * fill_rate
        net_proceeds_thb = gross_proceeds_thb - fee_thb
        pnl_thb = net_proceeds_thb - cost_basis_thb
        total_fee_thb = buy_fee_allocated_thb + fee_thb
        closed_trades.append(
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "symbol": symbol,
                "sell_coin_qty": matched_qty,
                "sell_rate": fill_rate,
                "buy_fee_thb": buy_fee_allocated_thb,
                "sell_fee_thb": fee_thb,
                "fee_thb": total_fee_thb,
                "gross_proceeds_thb": gross_proceeds_thb,
                "net_proceeds_thb": net_proceeds_thb,
                "cost_basis_thb": cost_basis_thb,
                "net_budget_thb": net_budget_allocated_thb,
                "gross_pnl_before_fees_thb": gross_proceeds_thb - net_budget_allocated_thb,
                "pnl_thb": pnl_thb,
            }
        )

    return filled_orders, closed_trades


def _build_live_execution_trade_history(
    *,
    conn: sqlite3.Connection | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if conn is None:
        with _connect() as local_conn:
            rows = _fetch_filled_execution_order_rows(local_conn)
    else:
        rows = _fetch_filled_execution_order_rows(conn)
    return _build_live_execution_trade_history_from_rows(rows)


def _summarize_live_execution_realized_from_history(
    *,
    today: str,
    filled_orders: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    today_rows = [
        row
        for row in closed_trades
        if str(row.get("updated_at") or row.get("created_at") or "").startswith(f"{today}")
    ]
    today_filled_orders = [
        row
        for row in filled_orders
        if str(row.get("event_time") or "").startswith(f"{today}")
    ]
    wins = sum(1 for row in today_rows if float(row.get("pnl_thb", 0.0)) > 0)
    losses = sum(1 for row in today_rows if float(row.get("pnl_thb", 0.0)) <= 0)

    symbol_summary_today: dict[str, dict[str, Any]] = {}
    for row in today_filled_orders:
        summary = symbol_summary_today.setdefault(
            str(row["symbol"]),
            {
                "symbol": str(row["symbol"]),
                "live_fee_thb": 0.0,
                "live_filled_orders": 0,
                "live_realized_pnl_thb": 0.0,
                "live_closed_trades": 0,
            },
        )
        summary["live_fee_thb"] += float(row.get("fee_thb", 0.0) or 0.0)
        summary["live_filled_orders"] += 1
    for row in today_rows:
        summary = symbol_summary_today.setdefault(
            str(row["symbol"]),
            {
                "symbol": str(row["symbol"]),
                "live_fee_thb": 0.0,
                "live_filled_orders": 0,
                "live_realized_pnl_thb": 0.0,
                "live_closed_trades": 0,
            },
        )
        summary["live_realized_pnl_thb"] += float(row.get("pnl_thb", 0.0) or 0.0)
        summary["live_closed_trades"] += 1

    return {
        "today": len(today_rows),
        "total": len(closed_trades),
        "today_realized_pnl": sum(float(row.get("pnl_thb", 0.0)) for row in today_rows),
        "total_realized_pnl": sum(float(row.get("pnl_thb", 0.0)) for row in closed_trades),
        "today_fee_thb": sum(float(row.get("fee_thb", 0.0)) for row in today_filled_orders),
        "total_fee_thb": sum(float(row.get("fee_thb", 0.0)) for row in filled_orders),
        "today_wins": wins,
        "today_losses": losses,
        "recent": list(reversed(closed_trades[-5:])),
        "recent_today": list(reversed(today_rows[-5:])),
        "symbol_summary_today": [symbol_summary_today[key] for key in sorted(symbol_summary_today)],
    }



def fetch_live_execution_realized_summary(*, today: str) -> dict[str, Any]:
    filled_orders, closed_trades = _build_live_execution_trade_history()
    return _summarize_live_execution_realized_from_history(
        today=today,
        filled_orders=filled_orders,
        closed_trades=closed_trades,
    )


def _build_daily_reporting_summary_from_history(
    *,
    conn: sqlite3.Connection,
    cutoff_day: str,
    symbol: str | None,
    filled_orders: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    trade_symbol_clause = "AND symbol = ?" if symbol else ""
    paper_rows = conn.execute(
        f"""
        SELECT
            substr(sell_time, 1, 10) AS report_date,
            COUNT(*) AS paper_trades,
            COALESCE(SUM(pnl_thb), 0) AS paper_pnl_thb,
            COALESCE(SUM(buy_fee_thb + sell_fee_thb), 0) AS paper_fee_thb,
            SUM(CASE WHEN pnl_thb > 0 THEN 1 ELSE 0 END) AS paper_wins,
            SUM(CASE WHEN pnl_thb <= 0 THEN 1 ELSE 0 END) AS paper_losses
        FROM paper_trade_logs
        WHERE sell_time >= ?
        {trade_symbol_clause}
        GROUP BY substr(sell_time, 1, 10)
        ORDER BY report_date DESC
        """,
        tuple(
            value
            for value in (f"{cutoff_day} 00:00:00", symbol)
            if value is not None
        ),
    ).fetchall()

    daily_rows: dict[str, dict[str, Any]] = {}

    def ensure_daily_row(report_date: str) -> dict[str, Any]:
        return daily_rows.setdefault(
            report_date,
            {
                "report_date": report_date,
                "paper_trades": 0,
                "paper_wins": 0,
                "paper_losses": 0,
                "paper_pnl_thb": 0.0,
                "paper_fee_thb": 0.0,
                "live_filled_orders": 0,
                "live_closed_trades": 0,
                "live_wins": 0,
                "live_losses": 0,
                "live_realized_pnl_thb": 0.0,
                "live_fee_thb": 0.0,
                "combined_closed_trades": 0,
                "combined_wins": 0,
                "combined_losses": 0,
                "combined_realized_pnl_thb": 0.0,
                "combined_fee_thb": 0.0,
            },
        )

    for row in paper_rows:
        report_date = str(row["report_date"] or "")
        if not report_date:
            continue
        summary = ensure_daily_row(report_date)
        summary["paper_trades"] = int(row["paper_trades"] or 0)
        summary["paper_wins"] = int(row["paper_wins"] or 0)
        summary["paper_losses"] = int(row["paper_losses"] or 0)
        summary["paper_pnl_thb"] = float(row["paper_pnl_thb"] or 0.0)
        summary["paper_fee_thb"] = float(row["paper_fee_thb"] or 0.0)

    for row in filled_orders:
        event_day = str(row.get("event_time") or "")[:10]
        if len(event_day) != 10 or event_day < cutoff_day:
            continue
        if symbol is not None and str(row.get("symbol")) != symbol:
            continue
        summary = ensure_daily_row(event_day)
        summary["live_filled_orders"] += 1
        summary["live_fee_thb"] += float(row.get("fee_thb", 0.0) or 0.0)

    for row in closed_trades:
        report_date = str(row.get("updated_at") or row.get("created_at") or "")[:10]
        if len(report_date) != 10 or report_date < cutoff_day:
            continue
        if symbol is not None and str(row.get("symbol")) != symbol:
            continue
        summary = ensure_daily_row(report_date)
        pnl_thb = float(row.get("pnl_thb", 0.0) or 0.0)
        summary["live_closed_trades"] += 1
        summary["live_realized_pnl_thb"] += pnl_thb
        if pnl_thb > 0:
            summary["live_wins"] += 1
        else:
            summary["live_losses"] += 1

    results: list[dict[str, Any]] = []
    for report_date in sorted(daily_rows, reverse=True):
        summary = daily_rows[report_date]
        summary["combined_closed_trades"] = int(summary["paper_trades"]) + int(
            summary["live_closed_trades"]
        )
        summary["combined_wins"] = int(summary["paper_wins"]) + int(summary["live_wins"])
        summary["combined_losses"] = int(summary["paper_losses"]) + int(
            summary["live_losses"]
        )
        summary["combined_realized_pnl_thb"] = float(summary["paper_pnl_thb"]) + float(
            summary["live_realized_pnl_thb"]
        )
        summary["combined_fee_thb"] = float(summary["paper_fee_thb"]) + float(
            summary["live_fee_thb"]
        )
        results.append(summary)

    return results


def fetch_daily_reporting_summary(
    *,
    days: int = 14,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    window_days = max(1, int(days))
    cutoff_day = format_date_text(now_dt() - timedelta(days=window_days - 1))
    with _connect() as conn:
        filled_orders, closed_trades = _build_live_execution_trade_history(conn=conn)
        return _build_daily_reporting_summary_from_history(
            conn=conn,
            cutoff_day=cutoff_day,
            symbol=symbol,
            filled_orders=filled_orders,
            closed_trades=closed_trades,
        )


def fetch_logs_page_dataset(
    *,
    today: str,
    runtime_limit: int = 200,
    telegram_limit: int = 50,
    audit_limit: int = 200,
) -> dict[str, Any]:
    with _connect() as conn:
        latest_account_snapshot = conn.execute(
            """
            SELECT id, created_at, source, private_api_status, capabilities_json, snapshot_json
            FROM account_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        runtime_rows = conn.execute(
            """
            SELECT id, created_at, event_type, severity, message, details_json
            FROM runtime_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(runtime_limit),),
        ).fetchall()
        error_rows = conn.execute(
            """
            SELECT id, created_at, event_type, severity, message, details_json
            FROM runtime_events
            WHERE severity = 'error'
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(runtime_limit),),
        ).fetchall()
        telegram_rows = conn.execute(
            """
            SELECT id, created_at, event_type, title, body, payload_json, status
            FROM telegram_outbox
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(telegram_limit),),
        ).fetchall()
        telegram_command_rows = conn.execute(
            """
            SELECT id, created_at, update_id, chat_id, username, command_text, status, response_text
            FROM telegram_command_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(telegram_limit),),
        ).fetchall()
        audit_rows = conn.execute(
            """
            SELECT
                id,
                created_at,
                action_type,
                actor_type,
                actor_id,
                source,
                target_type,
                target_id,
                symbol,
                old_value_json,
                new_value_json,
                status,
                message,
                reason,
                correlation_id,
                metadata_json
            FROM audit_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(audit_limit),),
        ).fetchall()
        paper_today_row = conn.execute(
            """
            SELECT
                COUNT(*) AS paper_trades,
                COALESCE(SUM(pnl_thb), 0) AS paper_pnl_thb,
                COALESCE(SUM(buy_fee_thb + sell_fee_thb), 0) AS paper_fee_thb
            FROM paper_trade_logs
            WHERE sell_time LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()
        filled_orders, closed_trades = _build_live_execution_trade_history(conn=conn)

    today_closed_trades = [
        row
        for row in closed_trades
        if str(row.get("updated_at") or row.get("created_at") or "").startswith(f"{today}")
    ]
    today_filled_orders = [
        row
        for row in filled_orders
        if str(row.get("event_time") or "").startswith(f"{today}")
    ]

    return {
        "latest_account_snapshot": (
            {
                "id": int(latest_account_snapshot["id"]),
                "created_at": latest_account_snapshot["created_at"],
                "source": latest_account_snapshot["source"],
                "private_api_status": latest_account_snapshot["private_api_status"],
                "capabilities": _load_json(
                    latest_account_snapshot["capabilities_json"], []
                ),
                "snapshot": _load_json(latest_account_snapshot["snapshot_json"], {}),
            }
            if latest_account_snapshot
            else None
        ),
        "historical_rows": [
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "severity": row["severity"],
                "message": row["message"],
                "details": _load_json(row["details_json"], {}),
            }
            for row in runtime_rows
        ],
        "error_rows": [
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "severity": row["severity"],
                "message": row["message"],
                "details": _load_json(row["details_json"], {}),
            }
            for row in error_rows
        ],
        "telegram_rows": [
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "title": row["title"],
                "body": row["body"],
                "payload": _load_json(row["payload_json"], {}),
                "status": row["status"],
            }
            for row in telegram_rows
        ],
        "telegram_command_rows": [dict(row) for row in telegram_command_rows],
        "audit_rows": [
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "action_type": row["action_type"],
                "actor_type": row["actor_type"],
                "actor_id": row["actor_id"],
                "source": row["source"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "symbol": row["symbol"],
                "old_value": _load_json(row["old_value_json"], None),
                "new_value": _load_json(row["new_value_json"], None),
                "status": row["status"],
                "message": row["message"],
                "reason": row["reason"],
                "correlation_id": row["correlation_id"],
                "metadata": _load_json(row["metadata_json"], {}),
            }
            for row in audit_rows
        ],
        "today_reporting": {
            "paper_trades": int((paper_today_row or {})["paper_trades"] or 0)
            if paper_today_row
            else 0,
            "paper_pnl_thb": float((paper_today_row or {})["paper_pnl_thb"] or 0.0)
            if paper_today_row
            else 0.0,
            "paper_fee_thb": float((paper_today_row or {})["paper_fee_thb"] or 0.0)
            if paper_today_row
            else 0.0,
            "live_closed_trades": len(today_closed_trades),
            "live_realized_pnl_thb": sum(
                float(row.get("pnl_thb", 0.0) or 0.0)
                for row in today_closed_trades
            ),
            "live_fee_thb": sum(
                float(row.get("fee_thb", 0.0) or 0.0)
                for row in today_filled_orders
            ),
        },
    }


def fetch_dashboard_summary(
    *,
    today: str,
    recent_signal_limit: int = 5,
    recent_market_limit: int = 5,
    recent_trade_limit: int = 5,
    recent_event_limit: int = 5,
) -> dict[str, Any]:
    with _connect() as conn:
        today_signal_totals = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM signal_logs
            WHERE created_at LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()
        total_signal_totals = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM signal_logs
            """
        ).fetchone()
        today_market_totals = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM market_snapshots
            WHERE created_at LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()
        total_market_totals = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM market_snapshots
            """
        ).fetchone()

        today_trade_totals = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(pnl_thb), 0) AS pnl_thb,
                COALESCE(SUM(buy_fee_thb + sell_fee_thb), 0) AS fee_thb
            FROM paper_trade_logs
            WHERE sell_time LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()
        total_trade_totals = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(pnl_thb), 0) AS pnl_thb,
                COALESCE(SUM(buy_fee_thb + sell_fee_thb), 0) AS fee_thb
            FROM paper_trade_logs
            """
        ).fetchone()

        recent_signals = conn.execute(
            """
            SELECT created_at, symbol, zone, status, last_price
            FROM signal_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_signal_limit,),
        ).fetchall()
        recent_market_snapshots = conn.execute(
            """
            SELECT created_at, symbol, last_price, zone, status, trading_mode
            FROM market_snapshots
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_market_limit,),
        ).fetchall()
        today_market_analytics_rows = conn.execute(
            """
            SELECT
                m.symbol AS symbol,
                COUNT(*) AS snapshots,
                MIN(m.last_price) AS min_price,
                MAX(m.last_price) AS max_price,
                (
                    SELECT ms.last_price
                    FROM market_snapshots ms
                    WHERE ms.symbol = m.symbol
                    AND ms.created_at LIKE ?
                    ORDER BY ms.id ASC
                    LIMIT 1
                ) AS first_price,
                (
                    SELECT ms.last_price
                    FROM market_snapshots ms
                    WHERE ms.symbol = m.symbol
                    AND ms.created_at LIKE ?
                    ORDER BY ms.id DESC
                    LIMIT 1
                ) AS latest_price,
                (
                    SELECT ms.zone
                    FROM market_snapshots ms
                    WHERE ms.symbol = m.symbol
                    AND ms.created_at LIKE ?
                    ORDER BY ms.id DESC
                    LIMIT 1
                ) AS latest_zone,
                (
                    SELECT ms.trading_mode
                    FROM market_snapshots ms
                    WHERE ms.symbol = m.symbol
                    AND ms.created_at LIKE ?
                    ORDER BY ms.id DESC
                    LIMIT 1
                ) AS latest_mode,
                (
                    SELECT ms.created_at
                    FROM market_snapshots ms
                    WHERE ms.symbol = m.symbol
                    AND ms.created_at LIKE ?
                    ORDER BY ms.id DESC
                    LIMIT 1
                ) AS latest_time
            FROM market_snapshots m
            WHERE m.created_at LIKE ?
            GROUP BY m.symbol
            ORDER BY m.symbol
            """,
            (
                f"{today}%",
                f"{today}%",
                f"{today}%",
                f"{today}%",
                f"{today}%",
                f"{today}%",
            ),
        ).fetchall()
        recent_trades = conn.execute(
            """
            SELECT sell_time, symbol, exit_reason, pnl_thb, pnl_percent
            FROM paper_trade_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_trade_limit,),
        ).fetchall()
        recent_events = conn.execute(
            """
            SELECT created_at, event_type, severity, message
            FROM runtime_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_event_limit,),
        ).fetchall()
        latest_account_snapshot = conn.execute(
            """
            SELECT created_at, source, private_api_status, capabilities_json
            FROM account_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_reconciliation = conn.execute(
            """
            SELECT created_at, phase, status, warnings_json, positions_count
            FROM reconciliation_results
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_execution_order = conn.execute(
            """
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, exchange_client_id, message, guardrails_json
            FROM execution_orders
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    market_analytics: list[dict[str, Any]] = []
    for row in today_market_analytics_rows:
        first_price = float(row["first_price"])
        latest_price = float(row["latest_price"])
        day_move_percent = (
            ((latest_price - first_price) / first_price) * 100 if first_price > 0 else 0.0
        )
        market_analytics.append(
            {
                "symbol": row["symbol"],
                "snapshots": int(row["snapshots"]),
                "first_price": first_price,
                "latest_price": latest_price,
                "min_price": float(row["min_price"]),
                "max_price": float(row["max_price"]),
                "day_move_percent": day_move_percent,
                "latest_zone": row["latest_zone"],
                "latest_mode": row["latest_mode"],
                "latest_time": row["latest_time"],
            }
        )

    live_execution_pnl = fetch_live_execution_realized_summary(today=today)

    return {
        "signals": {
            "today": int(today_signal_totals["count"] if today_signal_totals else 0),
            "total": int(total_signal_totals["count"] if total_signal_totals else 0),
            "recent": [dict(row) for row in recent_signals],
        },
        "market_snapshots": {
            "today": int(today_market_totals["count"] if today_market_totals else 0),
            "total": int(total_market_totals["count"] if total_market_totals else 0),
            "recent": [dict(row) for row in recent_market_snapshots],
            "analytics": market_analytics,
        },
        "paper_trades": {
            "today": int(today_trade_totals["count"] if today_trade_totals else 0),
            "total": int(total_trade_totals["count"] if total_trade_totals else 0),
            "today_realized_pnl": float(
                today_trade_totals["pnl_thb"] if today_trade_totals else 0.0
            ),
            "total_realized_pnl": float(
                total_trade_totals["pnl_thb"] if total_trade_totals else 0.0
            ),
            "today_fee_thb": float(
                today_trade_totals["fee_thb"] if today_trade_totals else 0.0
            ),
            "total_fee_thb": float(
                total_trade_totals["fee_thb"] if total_trade_totals else 0.0
            ),
            "recent": [dict(row) for row in recent_trades],
        },
        "live_execution_pnl": live_execution_pnl,
        "runtime_events": [dict(row) for row in recent_events],
        "latest_account_snapshot": (
            {
                "created_at": latest_account_snapshot["created_at"],
                "source": latest_account_snapshot["source"],
                "private_api_status": latest_account_snapshot["private_api_status"],
                "capabilities": _load_json(
                    latest_account_snapshot["capabilities_json"], []
                ),
            }
            if latest_account_snapshot
            else None
        ),
        "latest_reconciliation": (
            {
                "created_at": latest_reconciliation["created_at"],
                "phase": latest_reconciliation["phase"],
                "status": latest_reconciliation["status"],
                "warnings": _load_json(
                    latest_reconciliation["warnings_json"], []
                ),
                "positions_count": int(latest_reconciliation["positions_count"]),
            }
            if latest_reconciliation
            else None
        ),
        "latest_execution_order": (
            {
                "id": int(latest_execution_order["id"]),
                "created_at": latest_execution_order["created_at"],
                "updated_at": latest_execution_order["updated_at"],
                "symbol": latest_execution_order["symbol"],
                "side": latest_execution_order["side"],
                "order_type": latest_execution_order["order_type"],
                "state": latest_execution_order["state"],
                "exchange_order_id": latest_execution_order["exchange_order_id"],
                "exchange_client_id": latest_execution_order["exchange_client_id"],
                "message": latest_execution_order["message"],
                "guardrails": _load_json(latest_execution_order["guardrails_json"], {}),
            }
            if latest_execution_order
            else None
        ),
    }


def fetch_diagnostics_page_dataset(
    *,
    recent_order_limit: int = 10,
    recent_event_limit: int = 20,
) -> dict[str, Any]:
    table_names = (
        "runtime_events",
        "signal_logs",
        "market_snapshots",
        "account_snapshots",
        "execution_orders",
        "execution_order_events",
    )
    terminal_states = ("filled", "canceled", "rejected", "failed")
    placeholders = ",".join("?" for _ in terminal_states)

    with _connect() as conn:
        table_counts = {
            table_name: int(
                conn.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()["count"]
            )
            for table_name in table_names
        }
        latest_cleanup = conn.execute(
            """
            SELECT created_at, message, details_json
            FROM runtime_events
            WHERE event_type = 'sqlite_retention_cleanup'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_account_snapshot = conn.execute(
            """
            SELECT created_at, source, private_api_status, capabilities_json
            FROM account_snapshots
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_reconciliation = conn.execute(
            """
            SELECT created_at, phase, status, warnings_json, positions_count
            FROM reconciliation_results
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_execution_order = conn.execute(
            """
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, exchange_client_id, message, guardrails_json
            FROM execution_orders
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        open_execution_order_rows = conn.execute(
            f"""
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, exchange_client_id, message
            FROM execution_orders
            WHERE state NOT IN ({placeholders})
            ORDER BY id DESC
            """,
            terminal_states,
        ).fetchall()
        recent_order_rows = conn.execute(
            """
            SELECT id
            FROM execution_orders
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_order_limit,),
        ).fetchall()
        recent_event_rows = conn.execute(
            """
            SELECT id
            FROM execution_order_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_event_limit,),
        ).fetchall()
        latest_filled_rows = conn.execute(
            """
            SELECT e.id, e.created_at, e.updated_at, e.symbol, e.side, e.order_type, e.state,
                   e.exchange_order_id, e.exchange_client_id, e.response_json, e.message
            FROM execution_orders e
            INNER JOIN (
                SELECT symbol, MAX(id) AS latest_id
                FROM execution_orders
                WHERE state = 'filled'
                GROUP BY symbol
            ) latest
            ON latest.symbol = e.symbol AND latest.latest_id = e.id
            ORDER BY e.id DESC
            """
        ).fetchall()

    db_exists = DB_PATH.exists()
    db_size_bytes = DB_PATH.stat().st_size if db_exists else 0
    latest_filled_execution_orders: dict[str, dict[str, Any]] = {}
    for row in latest_filled_rows:
        symbol = row["symbol"]
        if symbol in latest_filled_execution_orders:
            continue
        latest_filled_execution_orders[symbol] = {
            "id": int(row["id"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "symbol": symbol,
            "side": row["side"],
            "order_type": row["order_type"],
            "state": row["state"],
            "exchange_order_id": row["exchange_order_id"],
            "exchange_client_id": row["exchange_client_id"],
            "response_payload": _load_json(row["response_json"], {}),
            "message": row["message"],
        }

    return {
        "db_summary": {
            "db_exists": db_exists,
            "db_size_bytes": int(db_size_bytes),
            "table_counts": table_counts,
            "latest_cleanup": (
                {
                    "created_at": latest_cleanup["created_at"],
                    "message": latest_cleanup["message"],
                    "details": _load_json(latest_cleanup["details_json"], {}),
                }
                if latest_cleanup
                else None
            ),
        },
        "retention_status": fetch_retention_status_summary(),
        "summary": {
            "latest_account_snapshot": (
                {
                    "created_at": latest_account_snapshot["created_at"],
                    "source": latest_account_snapshot["source"],
                    "private_api_status": latest_account_snapshot["private_api_status"],
                    "capabilities": _load_json(
                        latest_account_snapshot["capabilities_json"], []
                    ),
                }
                if latest_account_snapshot
                else None
            ),
            "latest_reconciliation": (
                {
                    "created_at": latest_reconciliation["created_at"],
                    "phase": latest_reconciliation["phase"],
                    "status": latest_reconciliation["status"],
                    "warnings": _load_json(
                        latest_reconciliation["warnings_json"], []
                    ),
                    "positions_count": int(latest_reconciliation["positions_count"]),
                }
                if latest_reconciliation
                else None
            ),
            "latest_execution_order": (
                {
                    "id": int(latest_execution_order["id"]),
                    "created_at": latest_execution_order["created_at"],
                    "updated_at": latest_execution_order["updated_at"],
                    "symbol": latest_execution_order["symbol"],
                    "side": latest_execution_order["side"],
                    "order_type": latest_execution_order["order_type"],
                    "state": latest_execution_order["state"],
                    "exchange_order_id": latest_execution_order["exchange_order_id"],
                    "exchange_client_id": latest_execution_order["exchange_client_id"],
                    "message": latest_execution_order["message"],
                    "guardrails": _load_json(latest_execution_order["guardrails_json"], {}),
                }
                if latest_execution_order
                else None
            ),
        },
        "execution_console_counts": {
            "open_orders": len(open_execution_order_rows),
            "recent_orders": len(recent_order_rows),
            "recent_events": len(recent_event_rows),
        },
        "open_execution_orders": [
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "symbol": row["symbol"],
                "side": row["side"],
                "order_type": row["order_type"],
                "state": row["state"],
                "exchange_order_id": row["exchange_order_id"],
                "exchange_client_id": row["exchange_client_id"],
                "message": row["message"],
            }
            for row in open_execution_order_rows
        ],
        "latest_filled_execution_orders_by_symbol": latest_filled_execution_orders,
    }


def fetch_overview_summary(
    *,
    today: str,
) -> dict[str, Any]:
    report_day_start, report_day_end = _day_range_bounds(today)
    with _connect() as conn:
        today_trade_totals = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(pnl_thb), 0) AS pnl_thb,
                COALESCE(SUM(buy_fee_thb + sell_fee_thb), 0) AS fee_thb
            FROM paper_trade_logs
            WHERE sell_time >= ?
            AND sell_time < ?
            """,
            (report_day_start, report_day_end),
        ).fetchone()
        total_trade_totals = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(pnl_thb), 0) AS pnl_thb,
                COALESCE(SUM(buy_fee_thb + sell_fee_thb), 0) AS fee_thb
            FROM paper_trade_logs
            """
        ).fetchone()
        latest_execution_order = conn.execute(
            """
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, exchange_client_id, message, guardrails_json
            FROM execution_orders
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    live_execution_pnl = fetch_live_execution_realized_summary(today=today)

    return {
        "paper_trades": {
            "today": int(today_trade_totals["count"] if today_trade_totals else 0),
            "today_realized_pnl": float(
                today_trade_totals["pnl_thb"] if today_trade_totals else 0.0
            ),
            "today_fee_thb": float(
                today_trade_totals["fee_thb"] if today_trade_totals else 0.0
            ),
            "total": int(total_trade_totals["count"] if total_trade_totals else 0),
            "total_realized_pnl": float(
                total_trade_totals["pnl_thb"] if total_trade_totals else 0.0
            ),
            "total_fee_thb": float(
                total_trade_totals["fee_thb"] if total_trade_totals else 0.0
            ),
        },
        "live_execution_pnl": live_execution_pnl,
        "latest_execution_order": (
            {
                "id": int(latest_execution_order["id"]),
                "created_at": latest_execution_order["created_at"],
                "updated_at": latest_execution_order["updated_at"],
                "symbol": latest_execution_order["symbol"],
                "side": latest_execution_order["side"],
                "order_type": latest_execution_order["order_type"],
                "state": latest_execution_order["state"],
                "exchange_order_id": latest_execution_order["exchange_order_id"],
                "exchange_client_id": latest_execution_order["exchange_client_id"],
                "message": latest_execution_order["message"],
                "guardrails": _load_json(latest_execution_order["guardrails_json"], {}),
            }
            if latest_execution_order
            else None
        ),
    }


def fetch_reporting_summary(
    *,
    today: str,
    symbol: str | None = None,
    recent_trade_limit: int = 10,
    recent_execution_limit: int = 10,
    recent_auto_exit_limit: int = 10,
    recent_error_limit: int = 10,
    recent_journal_limit: int = 20,
) -> dict[str, Any]:
    with _connect() as conn:
        filled_orders, closed_trades = _build_live_execution_trade_history(conn=conn)
        refresh_daily_performance_metrics_from_history(conn=conn)
        return _build_reporting_summary_from_history(
            conn=conn,
            today=today,
            symbol=symbol,
            recent_trade_limit=recent_trade_limit,
            recent_execution_limit=recent_execution_limit,
            recent_auto_exit_limit=recent_auto_exit_limit,
            recent_error_limit=recent_error_limit,
            recent_journal_limit=recent_journal_limit,
            filled_orders=filled_orders,
            closed_trades=closed_trades,
        )


def _build_reporting_summary_from_history(
    *,
    conn: sqlite3.Connection,
    today: str,
    symbol: str | None,
    recent_trade_limit: int,
    recent_execution_limit: int,
    recent_auto_exit_limit: int,
    recent_error_limit: int,
    recent_journal_limit: int,
    filled_orders: list[dict[str, Any]],
    closed_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    report_day_start, report_day_end = _day_range_bounds(today)
    market_symbol_clause = "AND symbol = ?" if symbol else ""
    signal_symbol_clause = "AND symbol = ?" if symbol else ""
    trade_symbol_clause = "AND symbol = ?" if symbol else ""

    market_rows = conn.execute(
        f"""
        WITH day_market AS (
            SELECT id, symbol, last_price, zone
            FROM market_snapshots
            WHERE created_at >= ?
            AND created_at < ?
            {market_symbol_clause}
        ),
        ranked_market AS (
            SELECT
                symbol,
                COUNT(*) OVER (PARTITION BY symbol) AS snapshots,
                MIN(last_price) OVER (PARTITION BY symbol) AS min_price,
                MAX(last_price) OVER (PARTITION BY symbol) AS max_price,
                FIRST_VALUE(last_price) OVER (
                    PARTITION BY symbol
                    ORDER BY id ASC
                ) AS first_price,
                FIRST_VALUE(last_price) OVER (
                    PARTITION BY symbol
                    ORDER BY id DESC
                ) AS latest_price,
                FIRST_VALUE(zone) OVER (
                    PARTITION BY symbol
                    ORDER BY id DESC
                ) AS latest_zone,
                ROW_NUMBER() OVER (
                    PARTITION BY symbol
                    ORDER BY id DESC
                ) AS row_num
            FROM day_market
        )
        SELECT
            symbol,
            snapshots,
            min_price,
            max_price,
            first_price,
            latest_price,
            latest_zone
        FROM ranked_market
        WHERE row_num = 1
        ORDER BY symbol
        """,
        tuple(value for value in (report_day_start, report_day_end, symbol) if value is not None),
    ).fetchall()
    signal_rows = conn.execute(
        f"""
        SELECT symbol, COUNT(*) AS signals
        FROM signal_logs
        WHERE created_at >= ?
        AND created_at < ?
        {signal_symbol_clause}
        GROUP BY symbol
        """,
        tuple(
            value for value in (report_day_start, report_day_end, symbol) if value is not None
        ),
    ).fetchall()
    trade_rows = conn.execute(
        f"""
        SELECT
            symbol,
            COUNT(*) AS trades,
            COALESCE(SUM(pnl_thb), 0) AS pnl_thb,
            COALESCE(SUM(buy_fee_thb + sell_fee_thb), 0) AS fee_thb,
            SUM(CASE WHEN pnl_thb > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN pnl_thb <= 0 THEN 1 ELSE 0 END) AS losses
        FROM paper_trade_logs
        WHERE sell_time >= ?
        AND sell_time < ?
        {trade_symbol_clause}
        GROUP BY symbol
        """,
        tuple(
            value for value in (report_day_start, report_day_end, symbol) if value is not None
        ),
    ).fetchall()
    recent_trades = conn.execute(
        f"""
        SELECT sell_time, symbol, exit_reason, pnl_thb, pnl_percent
        FROM paper_trade_logs
        WHERE 1 = 1
        {trade_symbol_clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(
            value
            for value in (symbol, recent_trade_limit)
            if value is not None
        ),
    ).fetchall()
    recent_errors = conn.execute(
        """
        SELECT created_at, event_type, message
        FROM runtime_events
        WHERE severity = 'error'
        ORDER BY id DESC
        LIMIT ?
        """,
        (recent_error_limit,),
    ).fetchall()
    recent_execution_orders = conn.execute(
        f"""
        SELECT id, created_at, updated_at, symbol, side, order_type, state,
               exchange_order_id, message
        FROM execution_orders
        WHERE 1 = 1
        {trade_symbol_clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(
            value
            for value in (symbol, recent_execution_limit)
            if value is not None
        ),
    ).fetchall()
    recent_auto_exit_events = conn.execute(
        """
        SELECT created_at, severity, message
        FROM runtime_events
        WHERE event_type = 'auto_live_exit'
        ORDER BY id DESC
        LIMIT ?
        """,
        (recent_auto_exit_limit,),
    ).fetchall()
    recent_trade_journal = conn.execute(
        f"""
        SELECT id, created_at, trading_mode, channel, status, symbol, side,
               signal_reason, exit_reason, request_rate, latest_price,
               amount_thb, amount_coin
        FROM trade_journal
        WHERE 1 = 1
        {trade_symbol_clause}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple(
            value
            for value in (symbol, recent_journal_limit)
            if value is not None
        ),
    ).fetchall()

    live_execution_summary = _summarize_live_execution_realized_from_history(
        today=today,
        filled_orders=filled_orders,
        closed_trades=closed_trades,
    )
    live_symbol_rows = [
        row for row in list(live_execution_summary.get("symbol_summary_today") or [])
        if symbol is None or str(row.get("symbol")) == symbol
    ]

    symbol_summary: dict[str, dict[str, Any]] = {}
    for row in market_rows:
        first_price = float(row["first_price"])
        latest_price = float(row["latest_price"])
        day_move_percent = (
            ((latest_price - first_price) / first_price) * 100 if first_price > 0 else 0.0
        )
        symbol_summary[row["symbol"]] = {
            "symbol": row["symbol"],
            "snapshots": int(row["snapshots"]),
            "signals": 0,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "pnl_thb": 0.0,
            "paper_fee_thb": 0.0,
            "live_fee_thb": 0.0,
            "combined_fee_thb": 0.0,
            "live_realized_pnl_thb": 0.0,
            "live_closed_trades": 0,
            "first_price": first_price,
            "latest_price": latest_price,
            "min_price": float(row["min_price"]),
            "max_price": float(row["max_price"]),
            "day_move_percent": day_move_percent,
            "latest_zone": row["latest_zone"],
        }

    for row in signal_rows:
        summary = symbol_summary.setdefault(
            row["symbol"],
            {
                "symbol": row["symbol"],
                "snapshots": 0,
                "signals": 0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl_thb": 0.0,
                "paper_fee_thb": 0.0,
                "live_fee_thb": 0.0,
                "combined_fee_thb": 0.0,
                "live_realized_pnl_thb": 0.0,
                "live_closed_trades": 0,
                "first_price": 0.0,
                "latest_price": 0.0,
                "min_price": 0.0,
                "max_price": 0.0,
                "day_move_percent": 0.0,
                "latest_zone": "n/a",
            },
        )
        summary["signals"] = int(row["signals"])

    for row in trade_rows:
        summary = symbol_summary.setdefault(
            row["symbol"],
            {
                "symbol": row["symbol"],
                "snapshots": 0,
                "signals": 0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl_thb": 0.0,
                "paper_fee_thb": 0.0,
                "live_fee_thb": 0.0,
                "combined_fee_thb": 0.0,
                "live_realized_pnl_thb": 0.0,
                "live_closed_trades": 0,
                "first_price": 0.0,
                "latest_price": 0.0,
                "min_price": 0.0,
                "max_price": 0.0,
                "day_move_percent": 0.0,
                "latest_zone": "n/a",
            },
        )
        summary["trades"] = int(row["trades"])
        summary["wins"] = int(row["wins"] or 0)
        summary["losses"] = int(row["losses"] or 0)
        summary["pnl_thb"] = float(row["pnl_thb"])
        summary["paper_fee_thb"] = float(row["fee_thb"] or 0.0)
        summary["combined_fee_thb"] = float(summary.get("paper_fee_thb", 0.0)) + float(summary.get("live_fee_thb", 0.0))

    for row in live_symbol_rows:
        summary = symbol_summary.setdefault(
            str(row["symbol"]),
            {
                "symbol": str(row["symbol"]),
                "snapshots": 0,
                "signals": 0,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl_thb": 0.0,
                "paper_fee_thb": 0.0,
                "live_fee_thb": 0.0,
                "combined_fee_thb": 0.0,
                "live_realized_pnl_thb": 0.0,
                "live_closed_trades": 0,
                "first_price": 0.0,
                "latest_price": 0.0,
                "min_price": 0.0,
                "max_price": 0.0,
                "day_move_percent": 0.0,
                "latest_zone": "n/a",
            },
        )
        summary["live_fee_thb"] = float(row.get("live_fee_thb", 0.0) or 0.0)
        summary["combined_fee_thb"] = float(summary.get("paper_fee_thb", 0.0)) + float(summary.get("live_fee_thb", 0.0))
        summary["live_realized_pnl_thb"] = float(row.get("live_realized_pnl_thb", 0.0) or 0.0)
        summary["live_closed_trades"] = int(row.get("live_closed_trades", 0) or 0)

    return {
        "filter_symbol": symbol,
        "symbol_summary": [symbol_summary[key] for key in sorted(symbol_summary)],
        "recent_trades": [dict(row) for row in recent_trades],
        "recent_execution_orders": [dict(row) for row in recent_execution_orders],
        "recent_auto_exit_events": [dict(row) for row in recent_auto_exit_events],
        "recent_errors": [dict(row) for row in recent_errors],
        "recent_trade_journal": [dict(row) for row in recent_trade_journal],
        "live_execution_pnl": live_execution_summary,
    }


def fetch_reports_page_dataset(
    *,
    today: str,
    days: int = 14,
    symbol: str | None = None,
    recent_trade_limit: int = 10,
    recent_execution_limit: int = 10,
    recent_auto_exit_limit: int = 10,
    recent_error_limit: int = 10,
    recent_journal_limit: int = 20,
) -> dict[str, Any]:
    window_days = max(1, int(days))
    cutoff_day = format_date_text(now_dt() - timedelta(days=window_days - 1))

    with _connect() as conn:
        filled_orders, closed_trades = _build_live_execution_trade_history(conn=conn)
        refresh_daily_performance_metrics_from_history(conn=conn)
        report = _build_reporting_summary_from_history(
            conn=conn,
            today=today,
            symbol=symbol,
            recent_trade_limit=recent_trade_limit,
            recent_execution_limit=recent_execution_limit,
            recent_auto_exit_limit=recent_auto_exit_limit,
            recent_error_limit=recent_error_limit,
            recent_journal_limit=recent_journal_limit,
            filled_orders=filled_orders,
            closed_trades=closed_trades,
        )
        daily_summary = _build_daily_reporting_summary_from_history(
            conn=conn,
            cutoff_day=cutoff_day,
            symbol=symbol,
            filled_orders=filled_orders,
            closed_trades=closed_trades,
        )
        portfolio_daily_metrics = _fetch_portfolio_daily_metrics_from_conn(
            conn=conn,
            days=window_days,
        )
        strategy_daily_metrics = _fetch_strategy_daily_metrics_from_conn(
            conn=conn,
            days=window_days,
            symbol=symbol,
        )
    recent_validation_runs = fetch_recent_validation_runs(limit=8, symbol=symbol)
    recent_validation_slices = (
        fetch_validation_run_slices(
            validation_run_id=int(recent_validation_runs[0]["id"]),
            limit=24,
        )
        if recent_validation_runs
        else []
    )
    recent_validation_consistency_checks = fetch_recent_validation_consistency_checks(
        limit=8,
        symbol=symbol,
    )

    return {
        "report": report,
        "daily_summary": daily_summary,
        "portfolio_daily_metrics": portfolio_daily_metrics,
        "strategy_daily_metrics": strategy_daily_metrics,
        "recent_validation_runs": recent_validation_runs,
        "recent_validation_slices": recent_validation_slices,
        "recent_validation_consistency_checks": recent_validation_consistency_checks,
    }


def fetch_recent_telegram_outbox(
    *,
    limit: int = 50,
    status: str | None = None,
    newest_first: bool = True,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if status:
        clauses.append("status = ?")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    order_sql = "DESC" if newest_first else "ASC"

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, event_type, title, body, payload_json, status
            FROM telegram_outbox
            {where_clause}
            ORDER BY id {order_sql}
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _load_json(item.pop("payload_json", None), {})
        results.append(item)
    return results


def update_telegram_outbox_status(*, outbox_id: int, status: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE telegram_outbox
            SET status = ?
            WHERE id = ?
            """,
            (status, int(outbox_id)),
        )


def fetch_recent_telegram_command_log(*, limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, update_id, chat_id, username, command_text, status, response_text
            FROM telegram_command_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(row) for row in rows]


def fetch_recent_audit_events(
    *,
    limit: int = 200,
    action_type: str | None = None,
    actor_type: str | None = None,
    symbol: str | None = None,
    status: str | None = None,
    created_after: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if action_type:
        clauses.append("action_type = ?")
        params.append(str(action_type))
    if actor_type:
        clauses.append("actor_type = ?")
        params.append(str(actor_type))
    if symbol:
        clauses.append("symbol = ?")
        params.append(str(symbol))
    if status:
        clauses.append("status = ?")
        params.append(str(status))
    if created_after:
        clauses.append("created_at >= ?")
        params.append(_normalize_time_text(created_after))

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                id,
                created_at,
                action_type,
                actor_type,
                actor_id,
                source,
                target_type,
                target_id,
                symbol,
                old_value_json,
                new_value_json,
                status,
                message,
                reason,
                correlation_id,
                metadata_json
            FROM audit_events
            {where_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

    return [
        {
            **dict(row),
            "old_value": _load_json(row["old_value_json"], None),
            "new_value": _load_json(row["new_value_json"], None),
            "metadata": _load_json(row["metadata_json"], {}),
        }
        for row in rows
    ]


def fetch_recent_trade_journal(
    *,
    limit: int = 50,
    symbol: str | None = None,
    channel: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if symbol:
        clauses.append("symbol = ?")
        params.append(str(symbol))
    if channel:
        clauses.append("channel = ?")
        params.append(str(channel))
    if status:
        clauses.append("status = ?")
        params.append(str(status))

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, trading_mode, channel, status, symbol, side,
                   signal_reason, exit_reason, request_rate, latest_price,
                   amount_thb, amount_coin, details_json
            FROM trade_journal
            {where_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

    return [
        {
            **dict(row),
            "details": _load_json(row["details_json"], {}),
        }
        for row in rows
    ]


def fetch_recent_validation_runs(
    *,
    limit: int = 20,
    symbol: str | None = None,
    validation_type: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(str(symbol))
    if validation_type:
        clauses.append("validation_type = ?")
        params.append(str(validation_type))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, validation_type, status, symbol, data_source,
                   resolution, mode, date_from, date_to, train_window_days,
                   test_window_days, step_days, fee_rate, cooldown_seconds,
                   base_rule_json, summary_json, metadata_json
            FROM validation_runs
            {where_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

    return [
        {
            **dict(row),
            "base_rule": _load_json(row["base_rule_json"], {}),
            "summary": _load_json(row["summary_json"], {}),
            "metadata": _load_json(row["metadata_json"], {}),
        }
        for row in rows
    ]


def fetch_validation_run_slices(
    *,
    validation_run_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if validation_run_id is not None:
        clauses.append("validation_run_id = ?")
        params.append(int(validation_run_id))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, validation_run_id, slice_no, status, train_start_at,
                   train_end_at, test_start_at, test_end_at, selected_variant,
                   selected_rule_json, train_metrics_json, test_metrics_json,
                   train_result_hash, test_result_hash, notes_json
            FROM validation_run_slices
            {where_clause}
            ORDER BY validation_run_id DESC, slice_no ASC, id ASC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

    return [
        {
            **dict(row),
            "selected_rule": _load_json(row["selected_rule_json"], {}),
            "train_metrics": _load_json(row["train_metrics_json"], {}),
            "test_metrics": _load_json(row["test_metrics_json"], {}),
            "notes": _load_json(row["notes_json"], []),
        }
        for row in rows
    ]


def fetch_recent_validation_consistency_checks(
    *,
    limit: int = 20,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(str(symbol))
    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, validation_run_id, check_type, status, symbol,
                   data_source, resolution, window_start_at, window_end_at,
                   rule_json, details_json
            FROM validation_consistency_checks
            {where_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

    return [
        {
            **dict(row),
            "rule": _load_json(row["rule_json"], {}),
            "details": _load_json(row["details_json"], {}),
        }
        for row in rows
    ]


def fetch_runtime_event_log(
    *,
    limit: int = 200,
    severity: str | None = None,
    event_type: str | None = None,
) -> list[dict[str, Any]]:
    where_clauses: list[str] = []
    params: list[Any] = []

    if severity:
        where_clauses.append("severity = ?")
        params.append(severity)
    if event_type:
        where_clauses.append("event_type = ?")
        params.append(event_type)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    params.append(int(limit))

    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, event_type, severity, message, details_json
            FROM runtime_events
            {where_sql}
            ORDER BY id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "created_at": row["created_at"],
            "event_type": row["event_type"],
            "severity": row["severity"],
            "message": row["message"],
            "details": _load_json(row["details_json"], {}),
        }
        for row in rows
    ]


def fetch_open_execution_orders(*, include_payloads: bool = True) -> list[dict[str, Any]]:
    terminal_states = ("filled", "canceled", "rejected", "failed")
    placeholders = ",".join("?" for _ in terminal_states)
    payload_columns = """
                   request_json, response_json,
                   guardrails_json,
    """ if include_payloads else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, exchange_client_id,
                   {payload_columns}
                   message
            FROM execution_orders
            WHERE state NOT IN ({placeholders})
            ORDER BY id DESC
            """,
            terminal_states,
        ).fetchall()

    return [
        {
            "id": int(row["id"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "symbol": row["symbol"],
            "side": row["side"],
            "order_type": row["order_type"],
            "state": row["state"],
            "exchange_order_id": row["exchange_order_id"],
            "exchange_client_id": row["exchange_client_id"],
            "message": row["message"],
            **(
                {
                    "request_payload": _load_json(row["request_json"], {}),
                    "response_payload": _load_json(row["response_json"], {}),
                    "guardrails": _load_json(row["guardrails_json"], {}),
                }
                if include_payloads
                else {}
            ),
        }
        for row in rows
    ]


def fetch_latest_filled_execution_orders_by_symbol() -> dict[str, dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT e.id, e.created_at, e.updated_at, e.symbol, e.side, e.order_type, e.state,
                   e.exchange_order_id, e.exchange_client_id, e.request_json, e.response_json,
                   e.guardrails_json, e.message
            FROM execution_orders e
            INNER JOIN (
                SELECT symbol, MAX(id) AS latest_id
                FROM execution_orders
                WHERE state = 'filled'
                GROUP BY symbol
            ) latest
            ON latest.symbol = e.symbol AND latest.latest_id = e.id
            ORDER BY e.id DESC
            """
        ).fetchall()

    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row["symbol"]
        latest_by_symbol[symbol] = {
            "id": int(row["id"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "symbol": symbol,
            "side": row["side"],
            "order_type": row["order_type"],
            "state": row["state"],
            "exchange_order_id": row["exchange_order_id"],
            "exchange_client_id": row["exchange_client_id"],
            "request_payload": _load_json(row["request_json"], {}),
            "response_payload": _load_json(row["response_json"], {}),
            "guardrails": _load_json(row["guardrails_json"], {}),
            "message": row["message"],
        }

    return latest_by_symbol


def fetch_execution_console_summary(
    *,
    recent_order_limit: int = 10,
    recent_event_limit: int = 20,
    include_open_order_payloads: bool = True,
) -> dict[str, Any]:
    open_orders = fetch_open_execution_orders(include_payloads=include_open_order_payloads)

    with _connect() as conn:
        recent_orders = conn.execute(
            """
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, message
            FROM execution_orders
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_order_limit,),
        ).fetchall()
        recent_events = conn.execute(
            """
            SELECT execution_order_id, created_at, from_state, to_state, event_type, message
            FROM execution_order_events
            ORDER BY id DESC
            LIMIT ?
            """,
            (recent_event_limit,),
        ).fetchall()

    return {
        "open_orders": open_orders,
        "recent_orders": [dict(row) for row in recent_orders],
        "recent_events": [dict(row) for row in recent_events],
    }
