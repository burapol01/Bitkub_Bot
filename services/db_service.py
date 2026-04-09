import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from services.env_service import get_env_path
from utils.time_utils import coerce_time_text, format_date_text, format_time_text, now_dt

DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = get_env_path("BITKUB_DB_PATH", DEFAULT_DB_DIR / "bitkub.db")
DB_DIR = DB_PATH.parent
SQLITE_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = int(SQLITE_TIMEOUT_SECONDS * 1000)


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
        }


def _load_json(value: str | None, default: Any):
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


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
                net_coin_qty = max(0.0, (amount_thb - fee_thb) / fill_rate)
                if net_coin_qty > 0:
                    lots.append(
                        {
                            "coin_qty": net_coin_qty,
                            "cost_thb": amount_thb,
                            "buy_rate": fill_rate,
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
        while remaining_qty > 1e-12 and lots:
            current_lot = lots[0]
            lot_qty = float(current_lot.get("coin_qty") or 0.0)
            lot_cost = float(current_lot.get("cost_thb") or 0.0)
            if lot_qty <= 1e-12:
                lots.pop(0)
                continue
            consume_qty = min(remaining_qty, lot_qty)
            consume_ratio = consume_qty / lot_qty if lot_qty > 0 else 0.0
            cost_basis_thb += lot_cost * consume_ratio
            current_lot["coin_qty"] = max(0.0, lot_qty - consume_qty)
            current_lot["cost_thb"] = max(0.0, lot_cost - (lot_cost * consume_ratio))
            remaining_qty -= consume_qty
            if current_lot["coin_qty"] <= 1e-12:
                lots.pop(0)

        matched_qty = sell_coin_qty - remaining_qty
        if matched_qty <= 1e-12:
            continue

        gross_proceeds_thb = matched_qty * fill_rate
        net_proceeds_thb = gross_proceeds_thb - fee_thb
        pnl_thb = net_proceeds_thb - cost_basis_thb
        closed_trades.append(
            {
                "id": int(row["id"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "symbol": symbol,
                "sell_coin_qty": matched_qty,
                "sell_rate": fill_rate,
                "fee_thb": fee_thb,
                "gross_proceeds_thb": gross_proceeds_thb,
                "net_proceeds_thb": net_proceeds_thb,
                "cost_basis_thb": cost_basis_thb,
                "gross_pnl_before_fees_thb": gross_proceeds_thb - cost_basis_thb,
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
) -> dict[str, Any]:
    with _connect() as conn:
        filled_orders, closed_trades = _build_live_execution_trade_history(conn=conn)
        return _build_reporting_summary_from_history(
            conn=conn,
            today=today,
            symbol=symbol,
            recent_trade_limit=recent_trade_limit,
            recent_execution_limit=recent_execution_limit,
            recent_auto_exit_limit=recent_auto_exit_limit,
            recent_error_limit=recent_error_limit,
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
) -> dict[str, Any]:
    window_days = max(1, int(days))
    cutoff_day = format_date_text(now_dt() - timedelta(days=window_days - 1))

    with _connect() as conn:
        filled_orders, closed_trades = _build_live_execution_trade_history(conn=conn)
        report = _build_reporting_summary_from_history(
            conn=conn,
            today=today,
            symbol=symbol,
            recent_trade_limit=recent_trade_limit,
            recent_execution_limit=recent_execution_limit,
            recent_auto_exit_limit=recent_auto_exit_limit,
            recent_error_limit=recent_error_limit,
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

    return {
        "report": report,
        "daily_summary": daily_summary,
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


def fetch_open_execution_orders() -> list[dict[str, Any]]:
    terminal_states = ("filled", "canceled", "rejected", "failed")
    placeholders = ",".join("?" for _ in terminal_states)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, exchange_client_id, request_json, response_json,
                   guardrails_json, message
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
            "request_payload": _load_json(row["request_json"], {}),
            "response_payload": _load_json(row["response_json"], {}),
            "guardrails": _load_json(row["guardrails_json"], {}),
            "message": row["message"],
        }
        for row in rows
    ]


def fetch_latest_filled_execution_orders_by_symbol() -> dict[str, dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, updated_at, symbol, side, order_type, state,
                   exchange_order_id, exchange_client_id, request_json, response_json,
                   guardrails_json, message
            FROM execution_orders
            WHERE state = 'filled'
            ORDER BY id DESC
            """
        ).fetchall()

    latest_by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row["symbol"]
        if symbol in latest_by_symbol:
            continue
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
) -> dict[str, Any]:
    open_orders = fetch_open_execution_orders()

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
