from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

import requests

from services.db_service import (
    fetch_recent_telegram_command_log,
    fetch_recent_telegram_outbox,
    insert_telegram_outbox,
    update_telegram_outbox_status,
)
from utils.time_utils import now_dt, parse_time_text

DEFAULT_TELEGRAM_NOTIFY_EVENTS = [
    "config_reload",
    "safety_pause",
    "manual_live_order",
    "auto_live_entry",
    "auto_live_exit",
    "runtime_error",
]
TELEGRAM_MESSAGE_CHUNK_LIMIT = 3500
TELEGRAM_POLL_RETRIES = 3
TELEGRAM_POLL_BACKOFF_SECONDS = 1.0



def _parse_created_at(value: str) -> datetime | None:
    try:
        return parse_time_text(str(value))
    except Exception:
        return None


def _notification_cooldown_seconds(event_type: str) -> int:
    normalized = str(event_type or "").strip().lower()
    if normalized in {"auto_live_exit", "auto_live_entry"}:
        return 300
    if normalized in {"runtime_error", "safety_pause"}:
        return 180
    return 60


def _recent_duplicate_notification_exists(
    *,
    event_type: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None,
    cooldown_seconds: int,
) -> bool:
    now = now_dt()
    recent_rows = fetch_recent_telegram_outbox(limit=100, newest_first=True)
    normalized_event_type = str(event_type or "")
    normalized_title = str(title or "")
    normalized_body = str(body or "")
    normalized_symbol = str((payload or {}).get("symbol") or "")

    for row in recent_rows:
        if str(row.get("event_type") or "") != normalized_event_type:
            continue
        if str(row.get("title") or "") != normalized_title:
            continue

        row_payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        row_symbol = str((row_payload or {}).get("symbol") or "")

        if normalized_event_type in {"auto_live_exit", "auto_live_entry"} and normalized_symbol:
            if row_symbol != normalized_symbol:
                continue
        elif str(row.get("body") or "") != normalized_body:
            continue

        created_at = _parse_created_at(str(row.get("created_at") or ""))
        if created_at is None:
            continue
        if (now - created_at).total_seconds() <= cooldown_seconds:
            return True
    return False


def _recent_duplicate_direct_command_response_exists(
    *,
    body: str,
    cooldown_seconds: int,
) -> bool:
    now = now_dt()
    normalized_body = str(body or "")

    for row in fetch_recent_telegram_command_log(limit=50):
        response_text = str(row.get("response_text") or "")
        if response_text != normalized_body:
            continue

        created_at = _parse_created_at(str(row.get("created_at") or ""))
        if created_at is None:
            continue
        if (now - created_at).total_seconds() <= cooldown_seconds:
            return True
    return False


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


def _chunk_telegram_text(text: str, *, max_length: int = TELEGRAM_MESSAGE_CHUNK_LIMIT) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= max_length:
        return [normalized]

    chunks: list[str] = []
    current = ""

    for line in normalized.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= max_length:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = ""

        remainder = line
        while len(remainder) > max_length:
            split_at = remainder.rfind(" ", 0, max_length)
            if split_at <= 0:
                split_at = max_length
            chunks.append(remainder[:split_at].rstrip())
            remainder = remainder[split_at:].lstrip()
        current = remainder

    if current:
        chunks.append(current)

    return [chunk for chunk in chunks if chunk]


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
    cooldown_seconds = _notification_cooldown_seconds(event_type)
    if _recent_duplicate_notification_exists(
        event_type=event_type,
        title=str(title),
        body=body,
        payload=payload or {},
        cooldown_seconds=cooldown_seconds,
    ):
        return False
    if (
        str(event_type or "").strip().lower() == "config_reload"
        and _recent_duplicate_direct_command_response_exists(
            body=body,
            cooldown_seconds=cooldown_seconds,
        )
    ):
        return False

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
        "error_type": None,
    }
    if not settings["enabled"] or not settings["control_enabled"]:
        return result
    if not settings["control_ready"]:
        result["error_type"] = "config"
        if not settings["bot_token_present"]:
            result["errors"].append("TELEGRAM_BOT_TOKEN is missing")
        if not settings["control_chat_ids"]:
            result["errors"].append("TELEGRAM_ALLOWED_CHAT_IDS is missing")
        return result

    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    recent_logs = fetch_recent_telegram_command_log(limit=200)
    last_update_id = max((int(row["update_id"]) for row in recent_logs), default=0)
    params = {"offset": last_update_id + 1, "timeout": 0, "limit": int(limit)}

    payload: dict[str, Any] | None = None
    last_network_error: Exception | None = None
    for attempt in range(TELEGRAM_POLL_RETRIES):
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params=params,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            last_network_error = None
            break
        except (requests.Timeout, requests.ConnectionError) as e:
            last_network_error = e
            if attempt < TELEGRAM_POLL_RETRIES - 1:
                time.sleep(TELEGRAM_POLL_BACKOFF_SECONDS + attempt)
                continue
            break
        except Exception as e:
            result["error_type"] = "request"
            result["errors"].append(str(e))
            return result

    if last_network_error is not None:
        result["error_type"] = "network"
        result["errors"].append(str(last_network_error))
        return result
    if not isinstance(payload, dict):
        result["error_type"] = "response"
        result["errors"].append("Telegram getUpdates returned a non-JSON object")
        return result
    if not payload.get("ok", False):
        result["error_type"] = "response"
        result["errors"].append("Telegram getUpdates returned ok=false")
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
    message_parts = _chunk_telegram_text(text)
    if not message_parts:
        raise ValueError("missing Telegram text")

    for chat_id in chat_ids:
        for message_text in message_parts:
            payload = {
                "chat_id": chat_id,
                "text": message_text,
                "disable_web_page_preview": True,
            }
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
