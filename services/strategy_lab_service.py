from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from statistics import pstdev
from typing import Any

from services import db_service as db_service_module
from clients.bitkub_client import build_history_window, get_tradingview_history
from services.db_service import (
    SQLITE_TIMEOUT_SECONDS,
    configure_sqlite_connection,
    fetch_market_candle_coverage,
    fetch_market_candles,
    insert_validation_consistency_check,
    insert_validation_run,
    insert_validation_run_slice,
    update_validation_run,
    upsert_market_candles,
)
from utils.time_utils import format_time_text, from_timestamp, now_text, now_dt, parse_time_text

_TIME_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class ReplayPosition:
    buy_time: str
    buy_price: float
    budget_thb: float
    buy_fee_thb: float
    net_budget_thb: float
    coin_qty: float
    fee_rate: float
    stop_loss_percent: float
    take_profit_percent: float
    sell_above: float


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_service_module.DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    return configure_sqlite_connection(conn)


def _parse_time(value: str) -> datetime:
    return parse_time_text(value)


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _scale(value: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum:
        return 0.5
    return max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))


def _calc_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    total_trades = len(trades)
    wins = sum(1 for row in trades if float(row.get("pnl_thb", 0.0)) > 0)
    losses = sum(1 for row in trades if float(row.get("pnl_thb", 0.0)) <= 0)
    total_pnl = sum(float(row.get("pnl_thb", 0.0)) for row in trades)
    total_fee = sum(float(row.get("fee_thb", 0.0)) for row in trades)
    gross_pnl_before_fees = sum(float(row.get("gross_pnl_before_fees_thb", row.get("pnl_thb", 0.0))) for row in trades)
    gross_win = sum(float(row.get("pnl_thb", 0.0)) for row in trades if float(row.get("pnl_thb", 0.0)) > 0)
    gross_loss = abs(sum(float(row.get("pnl_thb", 0.0)) for row in trades if float(row.get("pnl_thb", 0.0)) < 0))
    hold_minutes = [float(row.get("hold_minutes", 0.0)) for row in trades]
    avg_win = _safe_div(gross_win, wins)
    avg_loss = _safe_div(gross_loss, losses)
    expectancy = _safe_div(total_pnl, total_trades)
    win_rate = _safe_div(wins * 100.0, total_trades)
    profit_factor = _safe_div(gross_win, gross_loss)
    fee_drag_percent = (_safe_div(total_fee * 100.0, abs(gross_pnl_before_fees)) if abs(gross_pnl_before_fees) > 0 else 0.0)
    return {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": win_rate,
        "total_pnl_thb": total_pnl,
        "gross_win_thb": gross_win,
        "gross_loss_thb": gross_loss,
        "gross_pnl_before_fees_thb": gross_pnl_before_fees,
        "total_fee_thb": total_fee,
        "avg_fee_thb": _safe_div(total_fee, total_trades),
        "fee_drag_percent": fee_drag_percent,
        "avg_pnl_thb": expectancy,
        "avg_win_thb": avg_win,
        "avg_loss_thb": avg_loss,
        "profit_factor": profit_factor,
        "avg_hold_minutes": _safe_div(sum(hold_minutes), len(hold_minutes)),
    }


def fetch_trade_analytics(*, symbol: str | None = None) -> dict[str, Any]:
    symbol_clause = "WHERE symbol = ?" if symbol else ""
    params: tuple[Any, ...] = (symbol,) if symbol else ()
    with _connect() as conn:
        trade_rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT buy_time, sell_time, symbol, exit_reason, budget_thb, buy_price,
                       sell_price, coin_qty, pnl_thb, pnl_percent, buy_fee_thb, sell_fee_thb
                FROM paper_trade_logs
                {symbol_clause}
                ORDER BY buy_time ASC
                """,
                params,
            ).fetchall()
        ]

    for row in trade_rows:
        buy_dt = _parse_time(str(row["buy_time"]))
        sell_dt = _parse_time(str(row["sell_time"]))
        row["hold_minutes"] = max(0.0, (sell_dt - buy_dt).total_seconds() / 60.0)
        row["fee_thb"] = float(row.get("buy_fee_thb", 0.0) or 0.0) + float(row.get("sell_fee_thb", 0.0) or 0.0)
        row["gross_pnl_before_fees_thb"] = float(row.get("pnl_thb", 0.0) or 0.0) + row["fee_thb"]

    by_symbol_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    exit_reason_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trade_rows:
        by_symbol_bucket[str(row["symbol"])].append(row)
        exit_reason_bucket[str(row["exit_reason"])].append(row)

    by_symbol = []
    for sym in sorted(by_symbol_bucket):
        metrics = _calc_metrics(by_symbol_bucket[sym])
        by_symbol.append({"symbol": sym, **metrics})

    exit_reason_summary = []
    for exit_reason in sorted(exit_reason_bucket):
        metrics = _calc_metrics(exit_reason_bucket[exit_reason])
        exit_reason_summary.append({"exit_reason": exit_reason, **metrics})

    return {
        "totals": _calc_metrics(trade_rows),
        "by_symbol": by_symbol,
        "by_exit_reason": exit_reason_summary,
        "recent_trades": list(reversed(trade_rows[-20:])),
    }


def fetch_market_snapshot_coverage(*, days: int = 30) -> list[dict[str, Any]]:
    cutoff = format_time_text(now_dt() - timedelta(days=days))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT symbol,
                   COUNT(*) AS snapshots,
                   MIN(created_at) AS first_seen,
                   MAX(created_at) AS last_seen,
                   MIN(last_price) AS min_price,
                   MAX(last_price) AS max_price
            FROM market_snapshots
            WHERE created_at >= ?
            GROUP BY symbol
            ORDER BY symbol
            """,
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def _resolve_range_bounds(
    *,
    days: int,
    start_at: str | None = None,
    end_at: str | None = None,
) -> tuple[str, str | None]:
    end_dt = _parse_time(end_at) if end_at else now_dt()
    start_dt = _parse_time(start_at) if start_at else end_dt - timedelta(days=days)
    return format_time_text(start_dt), format_time_text(end_dt) if end_at else None


def load_market_snapshots(
    *,
    symbol: str,
    days: int = 7,
    start_at: str | None = None,
    end_at: str | None = None,
) -> list[dict[str, Any]]:
    start_text, end_text = _resolve_range_bounds(
        days=days,
        start_at=start_at,
        end_at=end_at,
    )
    end_clause = "AND created_at < ?" if end_text else ""
    params: list[Any] = [symbol, start_text]
    if end_text:
        params.append(end_text)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT created_at, symbol, last_price, buy_below, sell_above, zone, status, trading_mode
            FROM market_snapshots
            WHERE symbol = ? AND created_at >= ?
            {end_clause}
            ORDER BY created_at ASC, id ASC
            """,
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def load_market_candle_rows(
    *,
    symbol: str,
    resolution: str,
    days: int,
    start_at: str | None = None,
    end_at: str | None = None,
) -> list[dict[str, Any]]:
    if start_at is None and end_at is None:
        rows = fetch_market_candles(
            symbol=symbol,
            resolution=resolution,
            lookback_days=days,
        )
    else:
        start_text, end_text = _resolve_range_bounds(
            days=days,
            start_at=start_at,
            end_at=end_at,
        )
        end_clause = "AND open_at < ?" if end_text else ""
        params: list[Any] = [symbol, resolution, start_text]
        if end_text:
            params.append(end_text)
        with _connect() as conn:
            rows = conn.execute(
                f"""
                SELECT symbol, resolution, open_time, open_at, open_price, high_price,
                       low_price, close_price, volume
                FROM market_candles
                WHERE symbol = ? AND resolution = ? AND open_at >= ?
                {end_clause}
                ORDER BY open_time ASC
                """,
                tuple(params),
            ).fetchall()
    return [
        {
            "created_at": str(row["open_at"]),
            "symbol": symbol,
            "last_price": float(row["close_price"]),
            "high_price": float(row["high_price"]),
            "low_price": float(row["low_price"]),
            "volume": float(row["volume"]),
            "resolution": resolution,
        }
        for row in rows
    ]


def sync_candles_for_symbols(
    *,
    symbols: list[str],
    resolution: str,
    days: int,
) -> dict[str, Any]:
    from_ts, to_ts = build_history_window(days=days)
    synced: list[dict[str, Any]] = []
    errors: list[str] = []

    for symbol in symbols:
        try:
            payload = get_tradingview_history(
                symbol=symbol,
                resolution=resolution,
                from_ts=from_ts,
                to_ts=to_ts,
            )
            if str(payload.get("s", "")).lower() != "ok":
                raise ValueError(f"history status={payload.get('s')}")

            candles: list[dict[str, Any]] = []
            times = list(payload.get("t", []))
            opens = list(payload.get("o", []))
            highs = list(payload.get("h", []))
            lows = list(payload.get("l", []))
            closes = list(payload.get("c", []))
            volumes = list(payload.get("v", []))
            count = min(len(times), len(opens), len(highs), len(lows), len(closes), len(volumes))
            for index in range(count):
                open_time = int(times[index])
                candles.append(
                    {
                        "open_time": open_time,
                        "open_at": format_time_text(from_timestamp(open_time)),
                        "open_price": float(opens[index]),
                        "high_price": float(highs[index]),
                        "low_price": float(lows[index]),
                        "close_price": float(closes[index]),
                        "volume": float(volumes[index]),
                    }
                )

            upsert_market_candles(symbol=symbol, resolution=resolution, candles=candles)
            coverage = {
                "symbol": symbol,
                "resolution": resolution,
                "candles": len(candles),
                "first_seen": candles[0]["open_at"] if candles else None,
                "last_seen": candles[-1]["open_at"] if candles else None,
            }
            synced.append(coverage)
        except Exception as e:
            errors.append(f"{symbol}: {e}")

    return {
        "resolution": resolution,
        "days": int(days),
        "synced": synced,
        "errors": errors,
    }


def build_coin_ranking(
    *,
    symbols: list[str],
    resolution: str,
    lookback_days: int,
    end_at: str | None = None,
) -> dict[str, Any]:
    raw_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for symbol in symbols:
        candles = load_market_candle_rows(
            symbol=symbol,
            resolution=resolution,
            days=lookback_days,
            end_at=end_at,
        )
        if len(candles) < 3:
            errors.append(f"{symbol}: not enough stored candles for ranking")
            continue

        closes = [float(row["last_price"]) for row in candles]
        highs = [float(row["high_price"]) for row in candles]
        lows = [float(row["low_price"]) for row in candles]
        volumes = [float(row.get("volume", 0.0) or 0.0) for row in candles]
        returns = []
        for prev_close, next_close in zip(closes[:-1], closes[1:]):
            returns.append(_safe_div(next_close - prev_close, prev_close) * 100.0)

        first_close = closes[0]
        last_close = closes[-1]
        max_high = max(highs)
        min_low = min(lows)
        momentum_pct = _safe_div(last_close - first_close, first_close) * 100.0
        volatility_pct = pstdev(returns) if len(returns) > 1 else 0.0
        range_percent = _safe_div(max_high - min_low, first_close) * 100.0
        position_in_range = _safe_div(last_close - min_low, max_high - min_low)
        avg_volume = _safe_div(sum(volumes), len(volumes))
        raw_rows.append(
            {
                "symbol": symbol,
                "candles": len(candles),
                "first_seen": candles[0]["created_at"],
                "last_seen": candles[-1]["created_at"],
                "first_close": first_close,
                "last_close": last_close,
                "momentum_pct": momentum_pct,
                "volatility_pct": volatility_pct,
                "range_percent": range_percent,
                "position_in_range": position_in_range,
                "avg_volume": avg_volume,
            }
        )

    if not raw_rows:
        return {
            "resolution": resolution,
            "lookback_days": int(lookback_days),
            "as_of": end_at,
            "rows": [],
            "coverage": fetch_market_candle_coverage(resolution=resolution),
            "errors": errors,
        }

    momentum_values = [row["momentum_pct"] for row in raw_rows]
    volatility_values = [row["volatility_pct"] for row in raw_rows]
    volume_values = [row["avg_volume"] for row in raw_rows]

    min_momentum, max_momentum = min(momentum_values), max(momentum_values)
    min_volatility, max_volatility = min(volatility_values), max(volatility_values)
    min_volume, max_volume = min(volume_values), max(volume_values)

    ranked_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        momentum_norm = _scale(row["momentum_pct"], min_momentum, max_momentum)
        stability_norm = 1.0 - _scale(row["volatility_pct"], min_volatility, max_volatility)
        volume_norm = _scale(row["avg_volume"], min_volume, max_volume)
        range_norm = max(0.0, min(1.0, float(row["position_in_range"])))
        score = (momentum_norm * 0.4 + range_norm * 0.25 + stability_norm * 0.2 + volume_norm * 0.15) * 100.0

        if row["momentum_pct"] > 3 and range_norm > 0.65:
            trend_bias = "bullish"
        elif row["momentum_pct"] < -3 and range_norm < 0.35:
            trend_bias = "weak"
        else:
            trend_bias = "mixed"

        ranked_rows.append(
            {
                "symbol": row["symbol"],
                "score": score,
                "trend_bias": trend_bias,
                "candles": row["candles"],
                "momentum_pct": row["momentum_pct"],
                "volatility_pct": row["volatility_pct"],
                "range_percent": row["range_percent"],
                "position_in_range": row["position_in_range"] * 100.0,
                "avg_volume": row["avg_volume"],
                "last_close": row["last_close"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
        )

    ranked_rows.sort(key=lambda item: item["score"], reverse=True)
    for index, row in enumerate(ranked_rows, start=1):
        row["rank"] = index

    return {
        "resolution": resolution,
        "lookback_days": int(lookback_days),
        "as_of": end_at,
        "rows": ranked_rows,
        "coverage": fetch_market_candle_coverage(resolution=resolution),
        "errors": errors,
    }


def _open_replay_position(*, price: float, timestamp: str, rule: dict[str, Any], fee_rate: float) -> ReplayPosition:
    budget_thb = float(rule["budget_thb"])
    buy_fee_thb = budget_thb * fee_rate
    net_budget_thb = budget_thb - buy_fee_thb
    coin_qty = net_budget_thb / price if price > 0 else 0.0
    return ReplayPosition(
        buy_time=timestamp,
        buy_price=float(price),
        budget_thb=budget_thb,
        buy_fee_thb=buy_fee_thb,
        net_budget_thb=net_budget_thb,
        coin_qty=coin_qty,
        fee_rate=fee_rate,
        stop_loss_percent=float(rule["stop_loss_percent"]),
        take_profit_percent=float(rule["take_profit_percent"]),
        sell_above=float(rule["sell_above"]),
    )


def _close_replay_position(*, symbol: str, price: float, timestamp: str, position: ReplayPosition, exit_reason: str) -> dict[str, Any]:
    gross_proceeds_thb = float(position.coin_qty) * float(price)
    sell_fee_thb = gross_proceeds_thb * float(position.fee_rate)
    net_proceeds_thb = gross_proceeds_thb - sell_fee_thb
    total_fee_thb = float(position.buy_fee_thb) + sell_fee_thb
    gross_pnl_before_fees_thb = gross_proceeds_thb - float(position.net_budget_thb)
    pnl_thb = net_proceeds_thb - float(position.budget_thb)
    pnl_percent = _safe_div(pnl_thb * 100.0, float(position.budget_thb))
    hold_minutes = max(0.0, (_parse_time(timestamp) - _parse_time(position.buy_time)).total_seconds() / 60.0)
    return {
        "symbol": symbol,
        "buy_time": position.buy_time,
        "sell_time": timestamp,
        "buy_price": float(position.buy_price),
        "sell_price": float(price),
        "coin_qty": float(position.coin_qty),
        "budget_thb": float(position.budget_thb),
        "buy_fee_thb": float(position.buy_fee_thb),
        "sell_fee_thb": sell_fee_thb,
        "fee_thb": total_fee_thb,
        "gross_pnl_before_fees_thb": gross_pnl_before_fees_thb,
        "pnl_thb": pnl_thb,
        "pnl_percent": pnl_percent,
        "hold_minutes": hold_minutes,
        "exit_reason": exit_reason,
    }


def _run_replay_from_rows(
    *,
    symbol: str,
    rows: list[dict[str, Any]],
    rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    source_label: str,
    empty_note: str,
    coverage_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not rows:
        return {
            "symbol": symbol,
            "days": None,
            "rule": dict(rule),
            "source": source_label,
            "bars": 0,
            "coverage": None,
            "trades": [],
            "metrics": _calc_metrics([]),
            "open_position": None,
            "notes": [empty_note],
        }

    position: ReplayPosition | None = None
    previous_zone: str | None = None
    cooldown_until: datetime | None = None
    trades_today: dict[str, int] = defaultdict(int)
    trades: list[dict[str, Any]] = []

    max_trades_per_day = int(rule["max_trades_per_day"])

    for row in rows:
        timestamp = str(row["created_at"])
        last_price = float(row["last_price"])
        buy_below = float(rule["buy_below"])
        sell_above = float(rule["sell_above"])
        zone = "BUY" if last_price <= buy_below else "SELL" if last_price >= sell_above else "WAIT"
        now_dt = _parse_time(timestamp)
        day_key = timestamp[:10]

        if position is None:
            zone_changed_to_buy = zone == "BUY" and previous_zone != "BUY"
            cooldown_active = cooldown_until is not None and now_dt < cooldown_until
            if zone_changed_to_buy and not cooldown_active and trades_today[day_key] < max_trades_per_day:
                position = _open_replay_position(
                    price=last_price,
                    timestamp=timestamp,
                    rule=rule,
                    fee_rate=fee_rate,
                )
            previous_zone = zone
            continue

        move_percent = _safe_div((last_price - position.buy_price) * 100.0, position.buy_price)
        exit_reason: str | None = None
        if move_percent <= -float(position.stop_loss_percent):
            exit_reason = "STOP_LOSS"
        elif move_percent >= float(position.take_profit_percent):
            exit_reason = "TAKE_PROFIT"
        elif last_price >= float(position.sell_above):
            exit_reason = "SELL_ZONE"

        if exit_reason:
            trades.append(
                _close_replay_position(
                    symbol=symbol,
                    price=last_price,
                    timestamp=timestamp,
                    position=position,
                    exit_reason=exit_reason,
                )
            )
            trades_today[day_key] += 1
            cooldown_until = now_dt + timedelta(seconds=int(cooldown_seconds))
            position = None

        previous_zone = zone

    coverage = {
        "first_seen": rows[0]["created_at"],
        "last_seen": rows[-1]["created_at"],
        "min_price": min(float(row["last_price"]) for row in rows),
        "max_price": max(float(row["last_price"]) for row in rows),
    }
    if coverage_extra:
        coverage.update(coverage_extra)

    open_position_summary = None
    if position is not None:
        last_row = rows[-1]
        last_price = float(last_row["last_price"])
        open_position_summary = {
            "buy_time": position.buy_time,
            "buy_price": float(position.buy_price),
            "last_price": last_price,
            "unrealized_pnl_thb": (position.coin_qty * last_price) * (1 - position.fee_rate) - position.budget_thb,
            "hold_minutes": max(0.0, (_parse_time(str(last_row["created_at"])) - _parse_time(position.buy_time)).total_seconds() / 60.0),
        }

    return {
        "symbol": symbol,
        "days": None,
        "rule": {
            "buy_below": float(rule["buy_below"]),
            "sell_above": float(rule["sell_above"]),
            "budget_thb": float(rule["budget_thb"]),
            "stop_loss_percent": float(rule["stop_loss_percent"]),
            "take_profit_percent": float(rule["take_profit_percent"]),
            "max_trades_per_day": int(rule["max_trades_per_day"]),
        },
        "source": source_label,
        "bars": len(rows),
        "coverage": coverage,
        "trades": trades,
        "metrics": _calc_metrics(trades),
        "open_position": open_position_summary,
        "notes": [],
    }


def run_market_snapshot_replay(
    *,
    symbol: str,
    rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    days: int,
    start_at: str | None = None,
    end_at: str | None = None,
) -> dict[str, Any]:
    snapshots = load_market_snapshots(
        symbol=symbol,
        days=days,
        start_at=start_at,
        end_at=end_at,
    )
    result = _run_replay_from_rows(
        symbol=symbol,
        rows=snapshots,
        rule=rule,
        fee_rate=fee_rate,
        cooldown_seconds=cooldown_seconds,
        source_label="market_snapshots",
        empty_note="No market snapshots available for the selected symbol and lookback window.",
    )
    result["days"] = int(days)
    result["start_at"] = start_at
    result["end_at"] = end_at
    result["snapshots"] = result["bars"]
    return result


def run_market_candle_replay(
    *,
    symbol: str,
    resolution: str,
    rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    days: int,
    start_at: str | None = None,
    end_at: str | None = None,
) -> dict[str, Any]:
    candle_rows = load_market_candle_rows(
        symbol=symbol,
        resolution=resolution,
        days=days,
        start_at=start_at,
        end_at=end_at,
    )
    result = _run_replay_from_rows(
        symbol=symbol,
        rows=candle_rows,
        rule=rule,
        fee_rate=fee_rate,
        cooldown_seconds=cooldown_seconds,
        source_label="market_candles",
        empty_note="No stored candles available for the selected symbol, resolution, and lookback window. Sync candles first.",
        coverage_extra={"resolution": resolution},
    )
    result["days"] = int(days)
    result["resolution"] = resolution
    result["start_at"] = start_at
    result["end_at"] = end_at
    result["candles"] = result["bars"]
    return result


def build_validation_rule_variants(*, base_rule: dict[str, Any]) -> list[dict[str, Any]]:
    current_rule = {
        "buy_below": float(base_rule.get("buy_below", 0.0) or 0.0),
        "sell_above": float(base_rule.get("sell_above", 0.0) or 0.0),
        "budget_thb": float(base_rule.get("budget_thb", 100.0) or 100.0),
        "stop_loss_percent": float(base_rule.get("stop_loss_percent", 1.0) or 1.0),
        "take_profit_percent": float(base_rule.get("take_profit_percent", 1.2) or 1.2),
        "max_trades_per_day": int(base_rule.get("max_trades_per_day", 1) or 1),
    }
    current_buy = current_rule["buy_below"]
    current_sl = current_rule["stop_loss_percent"]
    current_tp = current_rule["take_profit_percent"]

    return [
        {
            "variant": "CURRENT",
            "note": "Current live rule from config.",
            "rule": dict(current_rule),
        },
        {
            "variant": "DEEPER_ENTRY_0_5",
            "note": "Wait for a slightly deeper dip before entering.",
            "rule": {**current_rule, "buy_below": current_buy * 0.995},
        },
        {
            "variant": "DEEPER_ENTRY_1_0",
            "note": "Wait for a deeper pullback before entering.",
            "rule": {**current_rule, "buy_below": current_buy * 0.99},
        },
        {
            "variant": "TIGHTER_STOP",
            "note": "Reduce downside tolerance and exit losses sooner.",
            "rule": {**current_rule, "stop_loss_percent": max(0.2, current_sl * 0.8)},
        },
        {
            "variant": "WIDER_TAKE_PROFIT",
            "note": "Allow more upside before taking profit.",
            "rule": {**current_rule, "take_profit_percent": max(0.2, current_tp * 1.35)},
        },
        {
            "variant": "FASTER_EXIT",
            "note": "Bring the sell target closer to realize gains sooner.",
            "rule": {
                **current_rule,
                "sell_above": max(
                    current_buy * (1.0 + max(current_tp * 0.7, 0.3) / 100.0),
                    current_buy * 1.003,
                ),
                "take_profit_percent": max(0.2, current_tp * 0.8),
            },
        },
    ]


def _canonicalize_for_hash(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_for_hash(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, list):
        return [_canonicalize_for_hash(item) for item in value]
    return value


def _result_hash(result: dict[str, Any]) -> str:
    payload = {
        "rule": result.get("rule"),
        "source": result.get("source"),
        "bars": result.get("bars"),
        "coverage": result.get("coverage"),
        "metrics": result.get("metrics"),
        "trades": result.get("trades"),
        "open_position": result.get("open_position"),
        "notes": result.get("notes"),
        "start_at": result.get("start_at"),
        "end_at": result.get("end_at"),
    }
    encoded = json.dumps(
        _canonicalize_for_hash(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _date_window_bounds(date_text: str) -> datetime:
    return _parse_time(date_text).replace(hour=0, minute=0, second=0, microsecond=0)


def generate_walk_forward_windows(
    *,
    date_from: str,
    date_to: str,
    train_window_days: int,
    test_window_days: int,
    step_days: int,
) -> list[dict[str, Any]]:
    if train_window_days <= 0 or test_window_days <= 0 or step_days <= 0:
        raise ValueError("train_window_days, test_window_days, and step_days must be > 0")

    overall_start = _date_window_bounds(date_from)
    overall_end = _date_window_bounds(date_to) + timedelta(days=1)
    windows: list[dict[str, Any]] = []
    train_start = overall_start
    slice_no = 1
    while True:
        train_end = train_start + timedelta(days=train_window_days)
        test_end = train_end + timedelta(days=test_window_days)
        if test_end > overall_end:
            break
        windows.append(
            {
                "slice_no": slice_no,
                "train_start_at": format_time_text(train_start),
                "train_end_at": format_time_text(train_end),
                "test_start_at": format_time_text(train_end),
                "test_end_at": format_time_text(test_end),
            }
        )
        train_start += timedelta(days=step_days)
        slice_no += 1
    return windows


def generate_time_series_cv_windows(
    *,
    date_from: str,
    date_to: str,
    train_window_days: int,
    test_window_days: int,
    step_days: int,
) -> list[dict[str, Any]]:
    if train_window_days <= 0 or test_window_days <= 0 or step_days <= 0:
        raise ValueError("train_window_days, test_window_days, and step_days must be > 0")

    overall_start = _date_window_bounds(date_from)
    overall_end = _date_window_bounds(date_to) + timedelta(days=1)
    train_end = overall_start + timedelta(days=train_window_days)
    windows: list[dict[str, Any]] = []
    slice_no = 1
    while True:
        test_end = train_end + timedelta(days=test_window_days)
        if test_end > overall_end:
            break
        windows.append(
            {
                "slice_no": slice_no,
                "train_start_at": format_time_text(overall_start),
                "train_end_at": format_time_text(train_end),
                "test_start_at": format_time_text(train_end),
                "test_end_at": format_time_text(test_end),
            }
        )
        train_end += timedelta(days=step_days)
        slice_no += 1
    return windows


def _run_validation_replay(
    *,
    symbol: str,
    data_source: str,
    resolution: str | None,
    rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    start_at: str,
    end_at: str,
    lookback_days: int,
) -> dict[str, Any]:
    if data_source == "candles":
        return run_market_candle_replay(
            symbol=symbol,
            resolution=str(resolution or "240"),
            rule=rule,
            fee_rate=fee_rate,
            cooldown_seconds=cooldown_seconds,
            days=lookback_days,
            start_at=start_at,
            end_at=end_at,
        )
    if data_source == "snapshots":
        return run_market_snapshot_replay(
            symbol=symbol,
            rule=rule,
            fee_rate=fee_rate,
            cooldown_seconds=cooldown_seconds,
            days=lookback_days,
            start_at=start_at,
            end_at=end_at,
        )
    raise ValueError(f"unsupported validation data_source: {data_source}")


def _select_validation_variant(
    *,
    symbol: str,
    data_source: str,
    resolution: str | None,
    mode: str,
    base_rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    train_start_at: str,
    train_end_at: str,
    train_window_days: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    variants = (
        [{"variant": "CURRENT", "note": "Current live rule from config.", "rule": dict(base_rule)}]
        if mode == "current_rule"
        else build_validation_rule_variants(base_rule=base_rule)
    )
    candidate_results: list[dict[str, Any]] = []
    for order, item in enumerate(variants):
        train_result = _run_validation_replay(
            symbol=symbol,
            data_source=data_source,
            resolution=resolution,
            rule=dict(item["rule"]),
            fee_rate=fee_rate,
            cooldown_seconds=cooldown_seconds,
            start_at=train_start_at,
            end_at=train_end_at,
            lookback_days=train_window_days,
        )
        metrics = dict(train_result.get("metrics") or {})
        candidate_results.append(
            {
                "variant": str(item["variant"]),
                "note": str(item.get("note") or ""),
                "rule": dict(item["rule"]),
                "variant_order": order,
                "result": train_result,
                "metrics": metrics,
            }
        )

    selected = max(
        candidate_results,
        key=lambda item: (
            int(item["metrics"].get("trades", 0) or 0) > 0,
            float(item["metrics"].get("total_pnl_thb", 0.0) or 0.0),
            float(item["metrics"].get("profit_factor", 0.0) or 0.0),
            float(item["metrics"].get("win_rate_percent", 0.0) or 0.0),
            -float(item["metrics"].get("fee_drag_percent", 0.0) or 0.0),
            -int(item["variant_order"]),
        ),
    )
    return selected, dict(selected["result"]), candidate_results


def _aggregate_validation_slices(
    *,
    validation_type: str,
    symbol: str,
    data_source: str,
    resolution: str | None,
    mode: str,
    slices: list[dict[str, Any]],
) -> dict[str, Any]:
    completed = [row for row in slices if str(row.get("status")) == "completed"]
    total_test_trades = sum(int(row["test_metrics"].get("trades", 0) or 0) for row in completed)
    total_test_wins = sum(int(row["test_metrics"].get("wins", 0) or 0) for row in completed)
    total_test_losses = sum(int(row["test_metrics"].get("losses", 0) or 0) for row in completed)
    total_test_pnl = sum(
        float(row["test_metrics"].get("total_pnl_thb", 0.0) or 0.0) for row in completed
    )
    total_test_fee = sum(
        float(row["test_metrics"].get("total_fee_thb", 0.0) or 0.0) for row in completed
    )
    total_test_gross_win = sum(
        float(row["test_metrics"].get("gross_win_thb", 0.0) or 0.0) for row in completed
    )
    total_test_gross_loss = sum(
        float(row["test_metrics"].get("gross_loss_thb", 0.0) or 0.0) for row in completed
    )
    total_train_pnl = sum(
        float(row["train_metrics"].get("total_pnl_thb", 0.0) or 0.0) for row in completed
    )

    cumulative_test_pnl = 0.0
    peak_test_pnl = 0.0
    worst_drawdown_thb = 0.0
    selected_variants: dict[str, int] = defaultdict(int)
    for row in completed:
        cumulative_test_pnl += float(row["test_metrics"].get("total_pnl_thb", 0.0) or 0.0)
        peak_test_pnl = max(peak_test_pnl, cumulative_test_pnl)
        worst_drawdown_thb = min(worst_drawdown_thb, cumulative_test_pnl - peak_test_pnl)
        selected_variant = str(row.get("selected_variant") or "n/a")
        selected_variants[selected_variant] += 1

    return {
        "validation_type": validation_type,
        "symbol": symbol,
        "data_source": data_source,
        "resolution": resolution,
        "mode": mode,
        "total_slices": len(slices),
        "completed_slices": len(completed),
        "skipped_slices": len(slices) - len(completed),
        "test_total_trades": total_test_trades,
        "test_total_wins": total_test_wins,
        "test_total_losses": total_test_losses,
        "test_total_pnl_thb": total_test_pnl,
        "test_total_fee_thb": total_test_fee,
        "test_win_rate_percent": _safe_div(total_test_wins * 100.0, total_test_trades),
        "test_profit_factor": _safe_div(total_test_gross_win, total_test_gross_loss),
        "train_total_pnl_thb": total_train_pnl,
        "cumulative_test_pnl_thb": cumulative_test_pnl,
        "worst_test_drawdown_thb": worst_drawdown_thb,
        "selected_variants": dict(sorted(selected_variants.items())),
    }


def _run_validation_framework(
    *,
    validation_type: str,
    symbol: str,
    data_source: str,
    resolution: str | None,
    mode: str,
    date_from: str,
    date_to: str,
    train_window_days: int,
    test_window_days: int,
    step_days: int,
    base_rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    windows: list[dict[str, Any]],
    persist: bool = True,
) -> dict[str, Any]:
    metadata = {
        "symbol": symbol,
        "data_source": data_source,
        "resolution": resolution,
        "mode": mode,
        "date_from": date_from,
        "date_to": date_to,
        "train_window_days": int(train_window_days),
        "test_window_days": int(test_window_days),
        "step_days": int(step_days),
        "fee_rate": float(fee_rate),
        "cooldown_seconds": int(cooldown_seconds),
        "windows": windows,
    }
    validation_run_id: int | None = None
    if persist:
        validation_run_id = insert_validation_run(
            created_at=now_text(),
            validation_type=validation_type,
            status="running",
            symbol=symbol,
            data_source=data_source,
            resolution=resolution,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            train_window_days=train_window_days,
            test_window_days=test_window_days,
            step_days=step_days,
            fee_rate=fee_rate,
            cooldown_seconds=cooldown_seconds,
            base_rule=base_rule,
            summary=None,
            metadata=metadata,
        )

    slices: list[dict[str, Any]] = []
    for window in windows:
        slice_notes: list[str] = []
        selected, train_result, candidate_results = _select_validation_variant(
            symbol=symbol,
            data_source=data_source,
            resolution=resolution,
            mode=mode,
            base_rule=base_rule,
            fee_rate=fee_rate,
            cooldown_seconds=cooldown_seconds,
            train_start_at=str(window["train_start_at"]),
            train_end_at=str(window["train_end_at"]),
            train_window_days=train_window_days,
        )
        train_metrics = dict(train_result.get("metrics") or {})
        if int(train_result.get("bars", 0) or 0) <= 0:
            slice_status = "skipped_no_train_data"
            test_result = {
                "metrics": _calc_metrics([]),
                "trades": [],
                "coverage": None,
                "open_position": None,
                "notes": ["No train data available for this slice."],
            }
            slice_notes.append("No train data available for this slice.")
            selected_variant = None
            selected_rule = None
            train_hash = None
            test_hash = None
            test_metrics = _calc_metrics([])
        else:
            selected_variant = str(selected["variant"])
            selected_rule = dict(selected["rule"])
            train_hash = _result_hash(train_result)
            test_result = _run_validation_replay(
                symbol=symbol,
                data_source=data_source,
                resolution=resolution,
                rule=selected_rule,
                fee_rate=fee_rate,
                cooldown_seconds=cooldown_seconds,
                start_at=str(window["test_start_at"]),
                end_at=str(window["test_end_at"]),
                lookback_days=test_window_days,
            )
            test_metrics = dict(test_result.get("metrics") or {})
            test_hash = _result_hash(test_result) if int(test_result.get("bars", 0) or 0) > 0 else None
            if int(test_result.get("bars", 0) or 0) <= 0:
                slice_status = "skipped_no_test_data"
                slice_notes.append("No test data available for this slice.")
            else:
                slice_status = "completed"
        slice_result = {
            "slice_no": int(window["slice_no"]),
            "status": slice_status,
            "train_start_at": str(window["train_start_at"]),
            "train_end_at": str(window["train_end_at"]),
            "test_start_at": str(window["test_start_at"]),
            "test_end_at": str(window["test_end_at"]),
            "selected_variant": selected_variant,
            "selected_rule": selected_rule,
            "train_metrics": train_metrics,
            "test_metrics": test_metrics,
            "train_result_hash": train_hash,
            "test_result_hash": test_hash,
            "candidate_variants": [
                {
                    "variant": str(item["variant"]),
                    "metrics": dict(item["metrics"]),
                }
                for item in candidate_results
            ],
            "notes": slice_notes,
        }
        slices.append(slice_result)
        if validation_run_id is not None:
            insert_validation_run_slice(
                validation_run_id=validation_run_id,
                slice_no=int(window["slice_no"]),
                status=slice_status,
                train_start_at=str(window["train_start_at"]),
                train_end_at=str(window["train_end_at"]),
                test_start_at=str(window["test_start_at"]),
                test_end_at=str(window["test_end_at"]),
                selected_variant=selected_variant,
                selected_rule=selected_rule,
                train_metrics=train_metrics,
                test_metrics=test_metrics,
                train_result_hash=train_hash,
                test_result_hash=test_hash,
                notes=slice_notes,
            )

    summary = _aggregate_validation_slices(
        validation_type=validation_type,
        symbol=symbol,
        data_source=data_source,
        resolution=resolution,
        mode=mode,
        slices=slices,
    )
    if validation_run_id is not None:
        update_validation_run(
            validation_run_id=validation_run_id,
            status="completed" if int(summary["completed_slices"]) > 0 else "no_data",
            summary=summary,
            metadata=metadata,
        )
    return {
        "validation_run_id": validation_run_id,
        "summary": summary,
        "slices": slices,
        "metadata": metadata,
    }


def run_walk_forward_validation(
    *,
    symbol: str,
    data_source: str,
    resolution: str | None,
    mode: str,
    date_from: str,
    date_to: str,
    train_window_days: int,
    test_window_days: int,
    step_days: int,
    base_rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    persist: bool = True,
) -> dict[str, Any]:
    windows = generate_walk_forward_windows(
        date_from=date_from,
        date_to=date_to,
        train_window_days=train_window_days,
        test_window_days=test_window_days,
        step_days=step_days,
    )
    return _run_validation_framework(
        validation_type="walk_forward",
        symbol=symbol,
        data_source=data_source,
        resolution=resolution,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        train_window_days=train_window_days,
        test_window_days=test_window_days,
        step_days=step_days,
        base_rule=base_rule,
        fee_rate=fee_rate,
        cooldown_seconds=cooldown_seconds,
        windows=windows,
        persist=persist,
    )


def run_time_series_cross_validation(
    *,
    symbol: str,
    data_source: str,
    resolution: str | None,
    mode: str,
    date_from: str,
    date_to: str,
    train_window_days: int,
    test_window_days: int,
    step_days: int,
    base_rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    persist: bool = True,
) -> dict[str, Any]:
    windows = generate_time_series_cv_windows(
        date_from=date_from,
        date_to=date_to,
        train_window_days=train_window_days,
        test_window_days=test_window_days,
        step_days=step_days,
    )
    return _run_validation_framework(
        validation_type="time_series_cv",
        symbol=symbol,
        data_source=data_source,
        resolution=resolution,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        train_window_days=train_window_days,
        test_window_days=test_window_days,
        step_days=step_days,
        base_rule=base_rule,
        fee_rate=fee_rate,
        cooldown_seconds=cooldown_seconds,
        windows=windows,
        persist=persist,
    )


def run_backtest_consistency_check(
    *,
    symbol: str,
    data_source: str,
    resolution: str | None,
    rule: dict[str, Any],
    fee_rate: float,
    cooldown_seconds: int,
    start_at: str,
    end_at: str,
    repetitions: int = 2,
    validation_run_id: int | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    hashes: list[str] = []
    issues: list[str] = []
    lookback_days = max(
        1,
        int((_parse_time(end_at) - _parse_time(start_at)).total_seconds() / 86400) + 1,
    )
    for _ in range(max(2, int(repetitions))):
        result = _run_validation_replay(
            symbol=symbol,
            data_source=data_source,
            resolution=resolution,
            rule=rule,
            fee_rate=fee_rate,
            cooldown_seconds=cooldown_seconds,
            start_at=start_at,
            end_at=end_at,
            lookback_days=lookback_days,
        )
        results.append(result)
        hashes.append(_result_hash(result))

        coverage = dict(result.get("coverage") or {})
        if coverage and str(coverage.get("last_seen") or "") >= str(end_at):
            issues.append("coverage last_seen reaches or exceeds the exclusive end_at bound")
        for trade in list(result.get("trades") or []):
            buy_time = str(trade.get("buy_time") or "")
            sell_time = str(trade.get("sell_time") or "")
            if buy_time and buy_time < str(start_at):
                issues.append("trade buy_time is earlier than start_at")
            if sell_time and sell_time >= str(end_at):
                issues.append("trade sell_time reaches or exceeds the exclusive end_at bound")

    status = "passed" if len(set(hashes)) == 1 and not issues else "failed"
    details = {
        "hashes": hashes,
        "issues": issues,
        "repetitions": len(hashes),
        "metrics": [dict(result.get("metrics") or {}) for result in results],
    }
    check_id = None
    if persist:
        check_id = insert_validation_consistency_check(
            created_at=now_text(),
            validation_run_id=validation_run_id,
            check_type="replay_determinism",
            status=status,
            symbol=symbol,
            data_source=data_source,
            resolution=resolution,
            window_start_at=start_at,
            window_end_at=end_at,
            rule=rule,
            details=details,
        )
    return {
        "id": check_id,
        "status": status,
        "hashes": hashes,
        "issues": issues,
        "details": details,
    }
