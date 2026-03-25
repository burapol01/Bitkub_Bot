from __future__ import annotations

from typing import Any

import streamlit as st

from clients.bitkub_client import get_market_symbols_v3
from services.strategy_lab_service import (
    run_market_candle_replay,
    run_market_snapshot_replay,
)


def normalize_market_symbol(raw_symbol: Any) -> str | None:
    value = str(raw_symbol or "").strip().upper().replace("-", "_")
    if not value:
        return None
    parts = value.split("_")
    if len(parts) != 2:
        return value
    left, right = parts
    if left == "THB":
        return f"THB_{right}"
    if right == "THB":
        return f"THB_{left}"
    return value


@st.cache_data(ttl=900, show_spinner=False)
def fetch_market_symbol_universe() -> dict[str, Any]:
    try:
        payload = get_market_symbols_v3()
        symbols: list[str] = []
        for row in payload:
            if isinstance(row, dict):
                raw_symbol = row.get("symbol") or row.get("id") or row.get("name")
            else:
                raw_symbol = row
            normalized = normalize_market_symbol(raw_symbol)
            if normalized and normalized not in symbols:
                symbols.append(normalized)
        symbols.sort()
        return {"symbols": symbols, "error": None}
    except Exception as e:
        return {"symbols": [], "error": str(e)}


def build_rule_seed(config: dict[str, Any], symbol: str) -> dict[str, Any]:
    existing = config["rules"].get(symbol)
    if existing:
        return dict(existing)
    return {
        "buy_below": 1.0,
        "sell_above": 1.1,
        "budget_thb": 100.0,
        "stop_loss_percent": 1.0,
        "take_profit_percent": 1.2,
        "max_trades_per_day": 1,
    }


def recommend_live_rule_action(
    *,
    auto_entry_pass: bool,
    replay_trades: int,
    replay_total_pnl: float,
    replay_win_rate: float,
) -> tuple[str, str]:
    if auto_entry_pass and replay_trades > 0 and replay_total_pnl > 0:
        return "KEEP", "Passes ranking gate and replay is profitable."
    if auto_entry_pass and replay_trades == 0:
        return "MONITOR", "Passes ranking gate but replay has no completed exits yet."
    if auto_entry_pass and replay_total_pnl <= 0:
        return "REVIEW", "Passes ranking gate but replay edge is weak or negative."
    if not auto_entry_pass and replay_trades > 0 and replay_total_pnl > 0 and replay_win_rate >= 50.0:
        return "REVIEW", "Replay is positive, but ranking gate is blocking this symbol."
    if not auto_entry_pass and replay_trades == 0:
        return "PRUNE", "Fails ranking gate and replay has no completed trades."
    if replay_total_pnl <= 0:
        return "PRUNE", "Fails ranking gate and replay is non-profitable."
    return "MONITOR", "Mixed signals across ranking and replay."


def build_live_rule_tuning_rows(
    *,
    config: dict[str, Any],
    ranking_rows: list[dict[str, Any]],
    ranking_resolution: str,
    ranking_days: int,
) -> list[dict[str, Any]]:
    ranking_map = {str(row["symbol"]): row for row in ranking_rows}
    rules = config.get("rules", {})
    allowed_biases = {
        str(value).strip().lower()
        for value in config.get("live_auto_entry_allowed_biases", ["bullish", "mixed"])
        if str(value).strip()
    } or {"bullish", "mixed"}
    min_score = float(config.get("live_auto_entry_min_score", 50.0))
    require_ranking = bool(config.get("live_auto_entry_require_ranking", True))
    fee_rate = float(config.get("fee_rate", 0.0025))
    cooldown_seconds = int(config.get("cooldown_seconds", 60))

    tuning_rows: list[dict[str, Any]] = []
    for symbol in sorted(rules):
        rule = dict(rules[symbol])
        ranking_row = ranking_map.get(symbol)
        if ranking_row:
            score = float(ranking_row.get("score", 0.0) or 0.0)
            trend_bias = str(ranking_row.get("trend_bias") or "n/a")
            last_close = float(ranking_row.get("last_close", 0.0) or 0.0)
            momentum_pct = float(ranking_row.get("momentum_pct", 0.0) or 0.0)
            row_bias = trend_bias.lower()
            score_pass = score >= min_score
            bias_pass = row_bias in allowed_biases
            if not require_ranking:
                auto_entry_pass = True
                gate_reason = "ranking gate disabled"
            elif not score_pass:
                auto_entry_pass = False
                gate_reason = f"score<{min_score:.1f}"
            elif not bias_pass:
                auto_entry_pass = False
                gate_reason = f"bias {trend_bias} blocked"
            else:
                auto_entry_pass = True
                gate_reason = "passes ranking gate"
        else:
            score = 0.0
            trend_bias = "n/a"
            last_close = 0.0
            momentum_pct = 0.0
            auto_entry_pass = not require_ranking
            gate_reason = "no stored ranking" if require_ranking else "ranking gate disabled"

        replay_result = run_market_candle_replay(
            symbol=symbol,
            resolution=str(ranking_resolution),
            rule=rule,
            fee_rate=fee_rate,
            cooldown_seconds=cooldown_seconds,
            days=int(ranking_days),
        )
        replay_metrics = dict(replay_result.get("metrics") or {})
        replay_trades = int(replay_metrics.get("trades", 0) or 0)
        replay_total_pnl = float(replay_metrics.get("total_pnl_thb", 0.0) or 0.0)
        replay_win_rate = float(replay_metrics.get("win_rate_percent", 0.0) or 0.0)
        replay_avg_hold = float(replay_metrics.get("avg_hold_minutes", 0.0) or 0.0)
        recommendation, note = recommend_live_rule_action(
            auto_entry_pass=auto_entry_pass,
            replay_trades=replay_trades,
            replay_total_pnl=replay_total_pnl,
            replay_win_rate=replay_win_rate,
        )
        open_position = replay_result.get("open_position") or {}

        tuning_rows.append(
            {
                "symbol": symbol,
                "recommendation": recommendation,
                "auto_entry_pass": "YES" if auto_entry_pass else "NO",
                "gate_reason": gate_reason,
                "score": score,
                "trend_bias": trend_bias,
                "momentum_pct": momentum_pct,
                "last_close": last_close,
                "buy_below": float(rule.get("buy_below", 0.0) or 0.0),
                "sell_above": float(rule.get("sell_above", 0.0) or 0.0),
                "budget_thb": float(rule.get("budget_thb", 0.0) or 0.0),
                "stop_loss_percent": float(rule.get("stop_loss_percent", 0.0) or 0.0),
                "take_profit_percent": float(rule.get("take_profit_percent", 0.0) or 0.0),
                "max_trades_per_day": int(rule.get("max_trades_per_day", 0) or 0),
                "replay_trades": replay_trades,
                "replay_pnl_thb": replay_total_pnl,
                "replay_win_rate": replay_win_rate,
                "replay_avg_hold_min": replay_avg_hold,
                "replay_open_position": "YES" if open_position else "NO",
                "tuning_note": note,
            }
        )

    tuning_rows.sort(
        key=lambda row: (
            {"KEEP": 0, "MONITOR": 1, "REVIEW": 2, "PRUNE": 3}.get(str(row["recommendation"]), 9),
            -float(row["score"]),
            str(row["symbol"]),
        )
    )
    return tuning_rows


def build_rule_compare_variants(*, base_rule: dict[str, Any]) -> list[dict[str, Any]]:
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
                "sell_above": max(current_buy * (1.0 + max(current_tp * 0.7, 0.3) / 100.0), current_buy * 1.003),
                "take_profit_percent": max(0.2, current_tp * 0.8),
            },
        },
    ]


def run_strategy_compare_rows(
    *,
    symbol: str,
    replay_source: str,
    replay_resolution: str,
    lookback_days: int,
    fee_rate: float,
    cooldown_seconds: int,
    variants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in variants:
        variant_name = str(item["variant"])
        note = str(item.get("note") or "")
        rule = dict(item["rule"])
        if replay_source == "candles":
            replay_result = run_market_candle_replay(
                symbol=symbol,
                resolution=str(replay_resolution),
                rule=rule,
                fee_rate=float(fee_rate),
                cooldown_seconds=int(cooldown_seconds),
                days=int(lookback_days),
            )
        else:
            replay_result = run_market_snapshot_replay(
                symbol=symbol,
                rule=rule,
                fee_rate=float(fee_rate),
                cooldown_seconds=int(cooldown_seconds),
                days=int(lookback_days),
            )
        metrics = dict(replay_result.get("metrics") or {})
        coverage = dict(replay_result.get("coverage") or {})
        rows.append(
            {
                "variant": variant_name,
                "buy_below": float(rule["buy_below"]),
                "sell_above": float(rule["sell_above"]),
                "budget_thb": float(rule["budget_thb"]),
                "stop_loss_percent": float(rule["stop_loss_percent"]),
                "take_profit_percent": float(rule["take_profit_percent"]),
                "max_trades_per_day": int(rule["max_trades_per_day"]),
                "trades": int(metrics.get("trades", 0) or 0),
                "wins": int(metrics.get("wins", 0) or 0),
                "losses": int(metrics.get("losses", 0) or 0),
                "win_rate_percent": float(metrics.get("win_rate_percent", 0.0) or 0.0),
                "total_pnl_thb": float(metrics.get("total_pnl_thb", 0.0) or 0.0),
                "avg_pnl_thb": float(metrics.get("avg_pnl_thb", 0.0) or 0.0),
                "profit_factor": float(metrics.get("profit_factor", 0.0) or 0.0),
                "avg_hold_minutes": float(metrics.get("avg_hold_minutes", 0.0) or 0.0),
                "open_position": "YES" if replay_result.get("open_position") else "NO",
                "bars": int(replay_result.get("candles", replay_result.get("snapshots", replay_result.get("bars", 0))) or 0),
                "coverage_last_seen": str(coverage.get("last_seen") or "n/a"),
                "note": note,
                "rule": rule,
            }
        )
    rows.sort(
        key=lambda row: (
            -float(row["total_pnl_thb"]),
            -float(row["win_rate_percent"]),
            -int(row["trades"]),
            str(row["variant"]),
        )
    )
    return rows


def annotate_strategy_compare_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []

    baseline = next((row for row in rows if str(row.get("variant")) == "CURRENT"), rows[0])
    baseline_pnl = float(baseline.get("total_pnl_thb", 0.0) or 0.0)
    baseline_trades = int(baseline.get("trades", 0) or 0)
    baseline_win_rate = float(baseline.get("win_rate_percent", 0.0) or 0.0)
    baseline_hold = float(baseline.get("avg_hold_minutes", 0.0) or 0.0)

    min_trades_required = max(3, min(baseline_trades if baseline_trades > 0 else 3, 5))
    min_bars_required = 20
    max_win_rate_drop = 12.5
    max_hold_increase_ratio = 1.5
    max_hold_increase_minutes = 360.0
    tie_pnl_gap = max(1.0, abs(baseline_pnl) * 0.12 + 0.25)
    clear_pnl_gap = max(2.0, abs(baseline_pnl) * 0.2 + 0.5)

    annotated: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        if str(current.get("variant")) == "CURRENT":
            current["decision"] = "Current baseline"
            current["decision_reason"] = "Use this row as the benchmark for the other variants."
            current["decision_rank"] = -1
            annotated.append(current)
            continue

        pnl = float(current.get("total_pnl_thb", 0.0) or 0.0)
        trades = int(current.get("trades", 0) or 0)
        win_rate = float(current.get("win_rate_percent", 0.0) or 0.0)
        hold_minutes = float(current.get("avg_hold_minutes", 0.0) or 0.0)
        bars = int(current.get("bars", 0) or 0)

        pnl_diff = pnl - baseline_pnl
        win_rate_diff = win_rate - baseline_win_rate
        hold_diff = hold_minutes - baseline_hold
        trades_enough = trades >= min_trades_required
        coverage_ok = bars >= min_bars_required
        hold_ok = (
            baseline_hold <= 0
            or hold_minutes <= max(baseline_hold * max_hold_increase_ratio, baseline_hold + max_hold_increase_minutes)
        )

        if not coverage_ok or not trades_enough:
            decision = "Needs more samples"
            reason = f"Only {trades} trade(s) and {bars} bar(s); wait for more replay data before promoting this variant."
        elif pnl_diff >= clear_pnl_gap and win_rate_diff >= -max_win_rate_drop and hold_ok:
            decision = "Clearly better"
            reason = f"PnL +{pnl_diff:,.2f} THB vs baseline with stable win rate and acceptable hold time."
        elif pnl_diff > 0 and win_rate_diff >= -max_win_rate_drop and hold_ok:
            decision = "Marginally better"
            reason = f"PnL +{pnl_diff:,.2f} THB vs baseline, but the edge is still modest."
        elif abs(pnl_diff) <= tie_pnl_gap and win_rate_diff >= -6.0 and (baseline_hold <= 0 or abs(hold_diff) <= max(90.0, baseline_hold * 0.3)):
            decision = "Tied with baseline"
            reason = "Performance is close to the current rule across PnL, win rate, and hold time."
        elif baseline_hold > 0 and hold_minutes < baseline_hold * 0.75 and pnl_diff < -tie_pnl_gap:
            decision = "Worse due to faster exit"
            reason = f"Exits faster than baseline, but gives up {abs(pnl_diff):,.2f} THB of replay PnL."
        else:
            decision = "Worse"
            if win_rate_diff < -max_win_rate_drop:
                reason = f"Win rate falls by {abs(win_rate_diff):.2f} points vs baseline."
            elif not hold_ok:
                reason = "Average hold time stretches too far beyond the baseline."
            else:
                reason = f"Replay PnL trails baseline by {abs(pnl_diff):,.2f} THB."

        current["decision"] = decision
        current["decision_reason"] = reason
        current["decision_rank"] = {
            "Clearly better": 0,
            "Marginally better": 1,
            "Tied with baseline": 2,
            "Needs more samples": 3,
            "Worse due to faster exit": 4,
            "Worse": 5,
        }.get(decision, 9)
        annotated.append(current)

    annotated.sort(
        key=lambda row: (
            int(row.get("decision_rank", 9)),
            -float(row.get("total_pnl_thb", 0.0) or 0.0),
            -float(row.get("win_rate_percent", 0.0) or 0.0),
            str(row.get("variant") or ""),
        )
    )
    return annotated
