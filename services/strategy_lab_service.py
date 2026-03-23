from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import pstdev
from typing import Any

from clients.bitkub_client import build_history_window, get_tradingview_history
from services.db_service import (
    DB_PATH,
    fetch_market_candle_coverage,
    fetch_market_candles,
    upsert_market_candles,
)

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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, _TIME_FMT)


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
    gross_win = sum(float(row.get("pnl_thb", 0.0)) for row in trades if float(row.get("pnl_thb", 0.0)) > 0)
    gross_loss = abs(sum(float(row.get("pnl_thb", 0.0)) for row in trades if float(row.get("pnl_thb", 0.0)) < 0))
    hold_minutes = [float(row.get("hold_minutes", 0.0)) for row in trades]
    avg_win = _safe_div(gross_win, wins)
    avg_loss = _safe_div(gross_loss, losses)
    expectancy = _safe_div(total_pnl, total_trades)
    win_rate = _safe_div(wins * 100.0, total_trades)
    profit_factor = _safe_div(gross_win, gross_loss)
    return {
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_percent": win_rate,
        "total_pnl_thb": total_pnl,
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
                       sell_price, coin_qty, pnl_thb, pnl_percent
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
    cutoff = (datetime.now() - timedelta(days=days)).strftime(_TIME_FMT)
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


def load_market_snapshots(*, symbol: str, days: int = 7) -> list[dict[str, Any]]:
    cutoff = (datetime.now() - timedelta(days=days)).strftime(_TIME_FMT)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT created_at, symbol, last_price, buy_below, sell_above, zone, status, trading_mode
            FROM market_snapshots
            WHERE symbol = ? AND created_at >= ?
            ORDER BY created_at ASC, id ASC
            """,
            (symbol, cutoff),
        ).fetchall()
    return [dict(row) for row in rows]


def load_market_candle_rows(*, symbol: str, resolution: str, days: int) -> list[dict[str, Any]]:
    rows = fetch_market_candles(symbol=symbol, resolution=resolution, lookback_days=days)
    return [
        {
            "created_at": str(row["open_at"]),
            "symbol": symbol,
            "last_price": float(row["close_price"]),
            "high_price": float(row["high_price"]),
            "low_price": float(row["low_price"]),
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
                        "open_at": datetime.fromtimestamp(open_time).strftime(_TIME_FMT),
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
) -> dict[str, Any]:
    raw_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for symbol in symbols:
        candles = fetch_market_candles(symbol=symbol, resolution=resolution, lookback_days=lookback_days)
        if len(candles) < 3:
            errors.append(f"{symbol}: not enough stored candles for ranking")
            continue

        closes = [float(row["close_price"]) for row in candles]
        highs = [float(row["high_price"]) for row in candles]
        lows = [float(row["low_price"]) for row in candles]
        volumes = [float(row["volume"]) for row in candles]
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
                "first_seen": candles[0]["open_at"],
                "last_seen": candles[-1]["open_at"],
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
) -> dict[str, Any]:
    snapshots = load_market_snapshots(symbol=symbol, days=days)
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
) -> dict[str, Any]:
    candle_rows = load_market_candle_rows(symbol=symbol, resolution=resolution, days=days)
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
    result["candles"] = result["bars"]
    return result
