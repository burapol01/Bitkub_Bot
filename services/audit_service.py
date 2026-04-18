from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from services.db_service import insert_audit_event
from services.env_service import APP_ROOT, get_env_path
from utils.time_utils import now_text

AUDIT_FALLBACK_PATH = get_env_path(
    "BITKUB_AUDIT_LOG_PATH",
    APP_ROOT / "data" / "audit_events.jsonl",
)
REDACTED_VALUE = "***REDACTED***"
SENSITIVE_KEY_PARTS = {
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "api_secret",
    "private_key",
    "client_secret",
    "access_key",
    "access_token",
    "refresh_token",
}


def new_correlation_id(prefix: str = "audit") -> str:
    normalized_prefix = "".join(
        ch if ch.isalnum() or ch in {"_", "-"} else "_"
        for ch in str(prefix or "audit").strip().lower()
    ).strip("_")
    if not normalized_prefix:
        normalized_prefix = "audit"
    return f"{normalized_prefix}_{secrets.token_hex(6)}"


def _path_is_sensitive(path: str) -> bool:
    normalized = str(path or "").strip().lower().replace("-", "_")
    if not normalized:
        return False
    parts = [part for part in normalized.split(".") if part]
    return any(
        any(sensitive_part in part for sensitive_part in SENSITIVE_KEY_PARTS)
        for part in parts
    )


def redact_value(value: Any, *, path: str = "") -> Any:
    if _path_is_sensitive(path):
        return REDACTED_VALUE if value is not None else None

    if isinstance(value, dict):
        return {
            str(key): redact_value(
                nested_value,
                path=f"{path}.{key}" if path else str(key),
            )
            for key, nested_value in value.items()
        }

    if isinstance(value, list):
        return [
            redact_value(item, path=f"{path}[]")
            for item in value
        ]

    return value


def build_config_change_maps(
    old_config: dict[str, Any] | None,
    new_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    old_map: dict[str, Any] = {}
    new_map: dict[str, Any] = {}
    changed_fields: list[str] = []

    def collect(old_value: Any, new_value: Any, path: str) -> None:
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            keys = sorted(set(old_value.keys()) | set(new_value.keys()))
            for key in keys:
                nested_path = f"{path}.{key}" if path else str(key)
                collect(old_value.get(key), new_value.get(key), nested_path)
            return

        if old_value == new_value:
            return

        changed_fields.append(path or "root")
        old_map[path or "root"] = redact_value(old_value, path=path)
        new_map[path or "root"] = redact_value(new_value, path=path)

    collect(old_config or {}, new_config, "")
    return old_map, new_map, changed_fields


def _append_fallback_line(record: dict[str, Any]) -> None:
    fallback_path: Path = AUDIT_FALLBACK_PATH
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    with open(fallback_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True))
        handle.write("\n")


def audit_event(
    *,
    action_type: str,
    actor_type: str,
    message: str,
    status: str,
    created_at: str | None = None,
    actor_id: str | None = None,
    source: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    symbol: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
    correlation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    record = {
        "created_at": created_at or now_text(),
        "action_type": str(action_type),
        "actor_type": str(actor_type),
        "actor_id": str(actor_id) if actor_id is not None else None,
        "source": str(source) if source is not None else None,
        "target_type": str(target_type) if target_type is not None else None,
        "target_id": str(target_id) if target_id is not None else None,
        "symbol": str(symbol) if symbol is not None else None,
        "old_value": redact_value(old_value),
        "new_value": redact_value(new_value),
        "status": str(status),
        "message": str(message),
        "reason": str(reason) if reason is not None else None,
        "correlation_id": str(correlation_id) if correlation_id is not None else None,
        "metadata": redact_value(metadata or {}),
    }

    try:
        insert_audit_event(**record)
        return
    except Exception as exc:
        print(f"[audit] sqlite write failed: {exc}")

    fallback_record = {
        **record,
        "fallback_reason": "sqlite_write_failed",
    }
    try:
        _append_fallback_line(fallback_record)
    except Exception as fallback_exc:
        print(f"[audit] fallback append failed: {fallback_exc}")


def audit_config_change(
    *,
    old_config: dict[str, Any] | None,
    new_config: dict[str, Any],
    actor_type: str,
    message: str,
    status: str = "succeeded",
    actor_id: str | None = None,
    source: str | None = None,
    reason: str | None = None,
    correlation_id: str | None = None,
    action_type: str = "config_update",
    metadata: dict[str, Any] | None = None,
) -> list[str]:
    old_map, new_map, changed_fields = build_config_change_maps(old_config, new_config)
    payload = dict(metadata or {})
    payload["changed_fields"] = list(changed_fields)
    if "mode" in new_map or "mode" in old_map:
        payload["mode_transition"] = {
            "from": (old_config or {}).get("mode"),
            "to": new_config.get("mode"),
        }

    audit_event(
        action_type=action_type,
        actor_type=actor_type,
        actor_id=actor_id,
        source=source,
        target_type="config",
        target_id="active",
        old_value=old_map,
        new_value=new_map,
        status=status,
        message=message,
        reason=reason,
        correlation_id=correlation_id,
        metadata=payload,
    )
    return changed_fields
