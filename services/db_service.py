import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "bitkub.db"


def _connect() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            """
        )


def _to_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=True)


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
            (created_at, event_type, severity, message, _to_json(details)),
        )


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
                created_at,
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
                created_at,
                symbol,
                last_price,
                buy_below,
                sell_above,
                zone,
                status,
                trading_mode,
            ),
        )


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
                created_at,
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
                created_at,
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
                created_at,
                updated_at,
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
                updated_at,
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
                created_at,
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
    cutoff_text = (
        datetime.now() - timedelta(days=retention_days)
    ).strftime("%Y-%m-%d %H:%M:%S")
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
        "paper_trade_logs",
        "account_snapshots",
        "reconciliation_results",
        "execution_orders",
        "execution_order_events",
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
                COALESCE(SUM(pnl_thb), 0) AS pnl_thb
            FROM paper_trade_logs
            WHERE sell_time LIKE ?
            """,
            (f"{today}%",),
        ).fetchone()
        total_trade_totals = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(pnl_thb), 0) AS pnl_thb
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
            "recent": [dict(row) for row in recent_trades],
        },
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


def fetch_reporting_summary(
    *,
    today: str,
    symbol: str | None = None,
    recent_trade_limit: int = 10,
    recent_execution_limit: int = 10,
    recent_auto_exit_limit: int = 10,
    recent_error_limit: int = 10,
) -> dict[str, Any]:
    like_today = f"{today}%"
    market_symbol_clause = "AND m.symbol = ?" if symbol else ""
    subquery_symbol_clause = "AND ms.symbol = ?" if symbol else ""
    signal_symbol_clause = "AND symbol = ?" if symbol else ""
    trade_symbol_clause = "AND symbol = ?" if symbol else ""
    error_details: dict[str, Any] = {"today": today}
    if symbol:
        error_details["symbol"] = symbol

    with _connect() as conn:
        market_rows = conn.execute(
            f"""
            SELECT
                m.symbol AS symbol,
                COUNT(*) AS snapshots,
                MIN(m.last_price) AS min_price,
                MAX(m.last_price) AS max_price,
                (
                    SELECT ms.last_price
                    FROM market_snapshots ms
                    WHERE ms.created_at LIKE ?
                    AND ms.symbol = m.symbol
                    {subquery_symbol_clause}
                    ORDER BY ms.id ASC
                    LIMIT 1
                ) AS first_price,
                (
                    SELECT ms.last_price
                    FROM market_snapshots ms
                    WHERE ms.created_at LIKE ?
                    AND ms.symbol = m.symbol
                    {subquery_symbol_clause}
                    ORDER BY ms.id DESC
                    LIMIT 1
                ) AS latest_price,
                (
                    SELECT ms.zone
                    FROM market_snapshots ms
                    WHERE ms.created_at LIKE ?
                    AND ms.symbol = m.symbol
                    {subquery_symbol_clause}
                    ORDER BY ms.id DESC
                    LIMIT 1
                ) AS latest_zone
            FROM market_snapshots m
            WHERE m.created_at LIKE ?
            {market_symbol_clause}
            GROUP BY m.symbol
            """,
            tuple(
                value
                for value in (
                    like_today,
                    symbol,
                    like_today,
                    symbol,
                    like_today,
                    symbol,
                    like_today,
                    symbol,
                )
                if value is not None
            ),
        ).fetchall()
        signal_rows = conn.execute(
            f"""
            SELECT symbol, COUNT(*) AS signals
            FROM signal_logs
            WHERE created_at LIKE ?
            {signal_symbol_clause}
            GROUP BY symbol
            """,
            tuple(value for value in (like_today, symbol) if value is not None),
        ).fetchall()
        trade_rows = conn.execute(
            f"""
            SELECT
                symbol,
                COUNT(*) AS trades,
                COALESCE(SUM(pnl_thb), 0) AS pnl_thb,
                SUM(CASE WHEN pnl_thb > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN pnl_thb <= 0 THEN 1 ELSE 0 END) AS losses
            FROM paper_trade_logs
            WHERE sell_time LIKE ?
            {trade_symbol_clause}
            GROUP BY symbol
            """,
            tuple(value for value in (like_today, symbol) if value is not None),
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

    return {
        "filter_symbol": symbol,
        "symbol_summary": [symbol_summary[key] for key in sorted(symbol_summary)],
        "recent_trades": [dict(row) for row in recent_trades],
        "recent_execution_orders": [dict(row) for row in recent_execution_orders],
        "recent_auto_exit_events": [dict(row) for row in recent_auto_exit_events],
        "recent_errors": [dict(row) for row in recent_errors],
    }


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
