import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_ENV_LOADED = False


def load_env_file(*, override: bool = False):
    global _ENV_LOADED

    if _ENV_LOADED and not override:
        return

    if not ENV_PATH.exists():
        _ENV_LOADED = True
        return

    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")

            if not key:
                continue

            if override or key not in os.environ:
                os.environ[key] = value

    _ENV_LOADED = True


def get_env(name: str, default: str | None = None) -> str | None:
    load_env_file()
    return os.getenv(name, default)


def get_bitkub_api_credentials() -> tuple[str | None, str | None]:
    api_key = get_env("BITKUB_API_KEY")
    api_secret = get_env("BITKUB_API_SECRET")
    return api_key, api_secret
