from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

ENV_CANDIDATES = (".env", "env_dev", "env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect Telegram env values and fetch recent bot updates so you can "
            "see which chat_id should be used in TELEGRAM_CHAT_ID."
        )
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="optional env file path; defaults to the first existing file from .env, env_dev, env",
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="skip Telegram getUpdates lookup and only print resolved env values",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="HTTP timeout in seconds for Telegram API calls",
    )
    return parser.parse_args()


def find_env_path(explicit_path: str) -> Path | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        return candidate if candidate.exists() else None

    for name in ENV_CANDIDATES:
        candidate = Path(name)
        if candidate.exists():
            return candidate
    return None


def load_env_file(env_path: Path) -> None:
    with env_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ[key] = value


def parse_chat_ids(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [part.strip() for part in str(raw_value).split(",") if part.strip()]


def resolve_settings() -> dict[str, Any]:
    token = str(os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    notify_chat_ids = parse_chat_ids(
        os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID")
    )
    control_chat_ids = parse_chat_ids(
        os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")
        or os.getenv("TELEGRAM_CONTROL_CHAT_IDS")
        or os.getenv("TELEGRAM_CHAT_IDS")
        or os.getenv("TELEGRAM_CHAT_ID")
    )
    return {
        "bot_token_present": bool(token),
        "notify_chat_ids": notify_chat_ids,
        "control_chat_ids": control_chat_ids,
        "token": token,
    }


def fetch_recent_chats(*, bot_token: str, timeout_seconds: int) -> list[dict[str, str]]:
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "requests is not installed. Install project dependencies before using Telegram API lookup."
        ) from exc

    response = requests.get(
        f"https://api.telegram.org/bot{bot_token}/getUpdates",
        params={"timeout": 0, "limit": 20},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or not payload.get("ok", False):
        raise RuntimeError("Telegram getUpdates did not return ok=true")

    recent_by_chat_id: dict[str, dict[str, str]] = {}
    for item in payload.get("result", []):
        message = (
            item.get("message")
            or item.get("edited_message")
            or item.get("channel_post")
            or item.get("edited_channel_post")
            or {}
        )
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            continue

        label = (
            str(chat.get("title") or "").strip()
            or str(chat.get("username") or "").strip()
            or str(chat.get("first_name") or "").strip()
            or str(chat.get("last_name") or "").strip()
            or "(no title)"
        )
        recent_by_chat_id[chat_id] = {
            "chat_id": chat_id,
            "chat_type": str(chat.get("type") or "").strip() or "unknown",
            "label": label,
            "text": str(message.get("text") or "").strip(),
        }

    return list(recent_by_chat_id.values())


def print_settings(settings: dict[str, Any], env_path: Path | None) -> None:
    print(f"Loaded env file: {env_path if env_path else '(none)'}")
    print(f"TELEGRAM_BOT_TOKEN present: {settings['bot_token_present']}")
    print(f"Notify chat ids: {settings['notify_chat_ids'] or '[]'}")
    print(f"Control chat ids: {settings['control_chat_ids'] or '[]'}")
    print()
    print("Use TELEGRAM_CHAT_ID when there is only one destination chat.")
    print("Use TELEGRAM_CHAT_IDS=id1,id2 when notifications should go to multiple chats.")
    print("Private chat ids are usually positive numbers; group or supergroup ids are usually negative.")


def print_recent_chats(chats: list[dict[str, str]]) -> None:
    print()
    if not chats:
        print("No recent Telegram updates found.")
        print("Send a message such as /start to your bot, then run this script again.")
        return

    print("Recent chats seen by the bot:")
    for chat in chats:
        message_preview = chat["text"][:60] if chat["text"] else "(no text payload)"
        print(
            f"- chat_id={chat['chat_id']} type={chat['chat_type']} "
            f"name={chat['label']} last_message={message_preview}"
        )


def main() -> int:
    args = parse_args()
    env_path = find_env_path(args.env_file)
    if args.env_file and env_path is None:
        print(f"Env file not found: {args.env_file}")
        return 1
    if env_path is not None:
        load_env_file(env_path)

    settings = resolve_settings()
    print_settings(settings, env_path)

    if args.skip_api:
        return 0
    if not settings["bot_token_present"]:
        print()
        print("Cannot query Telegram API because TELEGRAM_BOT_TOKEN is missing.")
        return 1

    try:
        chats = fetch_recent_chats(
            bot_token=settings["token"],
            timeout_seconds=max(1, int(args.timeout)),
        )
    except Exception as exc:
        print()
        print(f"Telegram API lookup failed: {exc}")
        return 1

    print_recent_chats(chats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
