import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_FILE_PATH = Path(__file__).resolve().parent.parent / "runtime_state.json"


def _replace_dict_contents(target: dict, source: dict):
    target.clear()
    target.update(source)


def _serialize_cooldowns(cooldowns: dict[str, datetime]) -> dict[str, str]:
    return {symbol: value.isoformat() for symbol, value in cooldowns.items()}


def _deserialize_cooldowns(raw_cooldowns: dict[str, Any]) -> dict[str, datetime]:
    cooldowns: dict[str, datetime] = {}

    for symbol, value in raw_cooldowns.items():
        if not isinstance(symbol, str) or not isinstance(value, str):
            continue
        cooldowns[symbol] = datetime.fromisoformat(value)

    return cooldowns


def load_runtime_state(
    last_zones: dict,
    positions: dict,
    daily_stats: dict,
    cooldowns: dict,
) -> tuple[bool, list[str]]:
    if not STATE_FILE_PATH.exists():
        return False, []

    try:
        with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        return False, [
            f"Runtime state file is invalid JSON at line {e.lineno}, column {e.colno}; starting with empty runtime state."
        ]
    except OSError as e:
        return False, [f"Unable to read runtime state file: {e}"]

    if not isinstance(payload, dict):
        return False, ["Runtime state file must contain a JSON object; starting with empty runtime state."]

    raw_last_zones = payload.get("last_zones", {})
    raw_positions = payload.get("positions", {})
    raw_daily_stats = payload.get("daily_stats", {})
    raw_cooldowns = payload.get("cooldowns", {})
    manual_pause = bool(payload.get("manual_pause", False))

    if not isinstance(raw_last_zones, dict):
        raw_last_zones = {}
    if not isinstance(raw_positions, dict):
        raw_positions = {}
    if not isinstance(raw_daily_stats, dict):
        raw_daily_stats = {}
    if not isinstance(raw_cooldowns, dict):
        raw_cooldowns = {}

    _replace_dict_contents(last_zones, raw_last_zones)
    _replace_dict_contents(positions, raw_positions)
    _replace_dict_contents(daily_stats, raw_daily_stats)
    _replace_dict_contents(cooldowns, _deserialize_cooldowns(raw_cooldowns))

    messages = [
        "Restored runtime state from runtime_state.json",
        f"open_positions={len(positions)} cooldowns={len(cooldowns)} tracked_days={len(daily_stats)}",
    ]
    if manual_pause:
        messages.append("manual pause flag was restored")

    return manual_pause, messages


def save_runtime_state(
    last_zones: dict,
    positions: dict,
    daily_stats: dict,
    cooldowns: dict,
    *,
    manual_pause: bool,
):
    payload = {
        "version": 1,
        "manual_pause": manual_pause,
        "last_zones": last_zones,
        "positions": positions,
        "daily_stats": daily_stats,
        "cooldowns": _serialize_cooldowns(cooldowns),
    }

    tmp_path = STATE_FILE_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)

    os.replace(tmp_path, STATE_FILE_PATH)
