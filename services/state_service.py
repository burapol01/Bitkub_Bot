import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_FILE_PATH = Path(__file__).resolve().parent.parent / "runtime_state.json"
STATE_PENDING_PATH = Path(__file__).resolve().parent.parent / "runtime_state.pending.json"
_STATE_WRITE_RETRIES = 10
_STATE_WRITE_RETRY_SECONDS = 0.1


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


def _candidate_state_paths() -> list[Path]:
    candidates = [path for path in [STATE_PENDING_PATH, STATE_FILE_PATH] if path.exists()]
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def _read_state_payload(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"Runtime state file {path.name} is invalid JSON at line {e.lineno}, column {e.colno}."
    except OSError as e:
        return None, f"Unable to read runtime state file {path.name}: {e}"

    if not isinstance(payload, dict):
        return None, f"Runtime state file {path.name} must contain a JSON object."
    return payload, None


def load_runtime_state(
    last_zones: dict,
    positions: dict,
    daily_stats: dict,
    cooldowns: dict,
) -> tuple[bool, list[str]]:
    state_paths = _candidate_state_paths()
    if not state_paths:
        return False, []

    payload: dict[str, Any] | None = None
    source_path: Path | None = None
    messages: list[str] = []

    for candidate in state_paths:
        candidate_payload, error_message = _read_state_payload(candidate)
        if candidate_payload is not None:
            payload = candidate_payload
            source_path = candidate
            break
        messages.append(error_message or f"Unable to load runtime state from {candidate.name}.")

    if payload is None or source_path is None:
        messages.append("Starting with empty runtime state.")
        return False, messages

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

    if source_path == STATE_PENDING_PATH:
        messages.append("Restored runtime state from runtime_state.pending.json")
    else:
        messages.append("Restored runtime state from runtime_state.json")
    messages.append(
        f"open_positions={len(positions)} cooldowns={len(cooldowns)} tracked_days={len(daily_stats)}"
    )
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

    tmp_path = STATE_FILE_PATH.with_suffix('.tmp')
    serialized = json.dumps(payload, indent=2, ensure_ascii=True)
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())

    for _ in range(_STATE_WRITE_RETRIES):
        try:
            os.replace(tmp_path, STATE_FILE_PATH)
            if STATE_PENDING_PATH.exists():
                try:
                    STATE_PENDING_PATH.unlink()
                except OSError:
                    pass
            return
        except PermissionError:
            time.sleep(_STATE_WRITE_RETRY_SECONDS)
        except OSError:
            time.sleep(_STATE_WRITE_RETRY_SECONDS)

    with open(STATE_PENDING_PATH, 'w', encoding='utf-8') as f:
        f.write(serialized)
        f.flush()
        os.fsync(f.fileno())

    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except OSError:
        pass
