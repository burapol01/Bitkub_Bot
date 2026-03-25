from __future__ import annotations

import os
import time
from typing import Any

import requests

from services.db_service import (
    fetch_recent_telegram_command_log,
    fetch_recent_telegram_outbox,
    insert_telegram_outbox,
    update_telegram_outbox_status,
)

DEFAULT_TELEGRAM_NOTIFY_EVENTS = [
    "config_reload",
    "safety_pause",
    "manual_live_order",
    "auto_live_entry",
    "auto_live_exit",
    "runtime_error",
]


def telegram_settings_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    notify_chat_ids = _parse_chat_ids(
        os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID")
    )
    control_chat_ids = _parse_chat_ids(
        os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")
        or os.getenv("TELEGRAM_CONTROL_CHAT_IDS")
        or os.getenv("TELEGRAM_CHAT_IDS")
        or os.getenv("TELEGRAM_CHAT_ID")
    )
    return {
        "enabled": bool(config.get("telegram_enabled", False)),
        "control_enabled": bool(config.get("telegram_control_enabled", False)),
        "notify_events": [
            str(event_name) for event_name in config.get("telegram_notify_events", [])
        ],
        "bot_token_present": bool(token),
        "chat_ids": notify_chat_ids,
        "control_chat_ids": control_chat_ids,
        "ready": bool(token) and bool(notify_chat_ids),
        "control_ready": bool(token) and bool(control_chat_ids),
    }


def _parse_chat_ids(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [
        part.strip()
        for part in str(raw_value).split(",")
        if part.strip()
    ]


def queue_telegram_notification(
    *,
    config: dict[str, Any],
    created_at: str,
    event_type: str,
    title: str,
    lines: list[str] | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    settings = telegram_settings_snapshot(config)
    if not settings["enabled"]:
        return False
    if event_type not in settings["notify_events"]:
        return False

    message_lines = [str(title).strip()]
    for line in lines or []:
        normalized = str(line).strip()
        if normalized:
            message_lines.append(normalized)

    body = "\n".join(message_lines)
    insert_telegram_outbox(
        created_at=created_at,
        event_type=event_type,
        title=str(title),
        body=body,
        payload=payload or {},
        status="queued",
    )
    return True


def flush_telegram_outbox(
    *,
    config: dict[str, Any],
    max_messages: int = 10,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    settings = telegram_settings_snapshot(config)
    queued_rows = fetch_recent_telegram_outbox(
        limit=max_messages,
        status="queued",
        newest_first=False,
    )
    result = {
        "enabled": settings["enabled"],
        "ready": settings["ready"],
        "queued": len(queued_rows),
        "sent": 0,
        "failed": 0,
        "errors": [],
    }

    if not settings["enabled"]:
        return result
    if not settings["ready"]:
        if not settings["bot_token_present"]:
            result["errors"].append("TELEGRAM_BOT_TOKEN is missing")
        if not settings["chat_ids"]:
            result["errors"].append("TELEGRAM_CHAT_IDS or TELEGRAM_CHAT_ID is missing")
        return result

    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    chat_ids = list(settings["chat_ids"])

    for row in queued_rows:
        try:
            _send_telegram_message(
                bot_token=token,
                chat_ids=chat_ids,
                text=str(row["body"]),
                timeout_seconds=timeout_seconds,
            )
        except Exception as e:
            update_telegram_outbox_status(outbox_id=int(row["id"]), status="failed")
            result["failed"] += 1
            result["errors"].append(
                f"id={int(row['id'])} event_type={row['event_type']}: {e}"
            )
            continue

        update_telegram_outbox_status(outbox_id=int(row["id"]), status="sent")
        result["sent"] += 1

    return result


def fetch_telegram_command_updates(
    *,
    config: dict[str, Any],
    limit: int = 20,
    timeout_seconds: int = 10,
) -> dict[str, Any]:
    settings = telegram_settings_snapshot(config)
    result = {
        "enabled": settings["enabled"],
        "control_enabled": settings["control_enabled"],
        "ready": settings["control_ready"],
        "updates": [],
        "errors": [],
    }
    if not settings["enabled"] or not settings["control_enabled"]:
        return result
    if not settings["control_ready"]:
        if not settings["bot_token_present"]:
            result["errors"].append("TELEGRAM_BOT_TOKEN is missing")
        if not settings["control_chat_ids"]:
            result["errors"].append("TELEGRAM_ALLOWED_CHAT_IDS is missing")
        return result

    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    recent_logs = fetch_recent_telegram_command_log(limit=200)
    last_update_id = max((int(row["update_id"]) for row in recent_logs), default=0)
    params = {"offset": last_update_id + 1, "timeout": 0, "limit": int(limit)}

    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params=params,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        result["errors"].append(str(e))
        return result

    updates: list[dict[str, Any]] = []
    for item in payload.get("result", []):
        message = item.get("message") or item.get("edited_message") or {}
        text = str(message.get("text") or "").strip()
        chat = message.get("chat") or {}
        if not text.startswith("/"):
            continue
        updates.append(
            {
                "update_id": int(item.get("update_id", 0)),
                "chat_id": str(chat.get("id") or ""),
                "chat_type": str(chat.get("type") or ""),
                "username": str((message.get("from") or {}).get("username") or ""),
                "first_name": str((message.get("from") or {}).get("first_name") or ""),
                "command_text": text,
            }
        )
    result["updates"] = updates
    return result


def telegram_chat_is_authorized(*, config: dict[str, Any], chat_id: str) -> bool:
    settings = telegram_settings_snapshot(config)
    return str(chat_id) in set(settings["control_chat_ids"])


def send_telegram_direct_message(
    *,
    config: dict[str, Any],
    chat_id: str,
    text: str,
    timeout_seconds: int = 20,
) -> None:
    settings = telegram_settings_snapshot(config)
    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not settings["bot_token_present"] or not token:
        raise ValueError("missing Telegram bot token")
    _send_telegram_message(
        bot_token=token,
        chat_ids=[str(chat_id)],
        text=str(text),
        timeout_seconds=timeout_seconds,
    )


def _send_telegram_message(
    *,
    bot_token: str,
    chat_ids: list[str],
    text: str,
    timeout_seconds: int,
) -> None:
    if not bot_token:
        raise ValueError("missing Telegram bot token")
    if not chat_ids:
        raise ValueError("missing Telegram chat ids")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload_template = {
        "text": text,
        "disable_web_page_preview": True,
    }

    for chat_id in chat_ids:
        payload = dict(payload_template)
        payload["chat_id"] = chat_id
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=timeout_seconds)
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or not body.get("ok", False):
                    raise RuntimeError(
                        f"Telegram sendMessage rejected payload for chat_id={chat_id}"
                    )
                last_error = None
                break
            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = e
                if attempt < 2:
                    time.sleep(1.0 + attempt)
                    continue
                break

        if last_error is not None:
            raise last_error
