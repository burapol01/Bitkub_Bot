import json
import sqlite3
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


def _load_json(value: str | None, default: Any):
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


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
    }
