import os
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
ENV_CANDIDATES = (".env", "env_dev", "env")
_ENV_LOADED = False
_ENV_PATH: Path | None = None


def _resolve_env_path() -> Path | None:
    explicit_path = os.getenv("BITKUB_ENV_FILE")
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if not candidate.is_absolute():
            candidate = APP_ROOT / candidate
        return candidate

    for name in ENV_CANDIDATES:
        candidate = APP_ROOT / name
        if candidate.exists():
            return candidate

    return None


def load_env_file(*, override: bool = False):
    global _ENV_LOADED, _ENV_PATH

    if _ENV_LOADED and not override:
        return

    env_path = _resolve_env_path()
    _ENV_PATH = env_path

    if env_path is None or not env_path.exists():
        _ENV_LOADED = True
        return

    with open(env_path, "r", encoding="utf-8") as f:
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


def get_loaded_env_path() -> Path | None:
    load_env_file()
    return _ENV_PATH


def get_env(name: str, default: str | None = None) -> str | None:
    load_env_file()
    return os.getenv(name, default)


def get_env_path(name: str, default: Path) -> Path:
    raw_value = get_env(name)
    if not raw_value:
        return default
    return Path(raw_value).expanduser()


def get_bitkub_api_credentials() -> tuple[str | None, str | None]:
    api_key = get_env("BITKUB_API_KEY")
    api_secret = get_env("BITKUB_API_SECRET")
    return api_key, api_secret
