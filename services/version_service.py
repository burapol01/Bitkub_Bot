from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.env_service import APP_ROOT

GIT_TIMEOUT_SECONDS = 2.0


def _app_root() -> Path:
    raw_root = os.getenv("BITKUB_APP_ROOT")
    if raw_root:
        return Path(raw_root).expanduser().resolve()
    return APP_ROOT.resolve()


def _run_git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    output = completed.stdout.strip()
    return output or None


@lru_cache(maxsize=1)
def get_app_version_snapshot() -> dict[str, Any]:
    root = _app_root()
    env_version = str(os.getenv("BITKUB_APP_VERSION") or "").strip()

    branch = _run_git(root, "branch", "--show-current")
    commit = _run_git(root, "rev-parse", "HEAD")
    commit_short = _run_git(root, "rev-parse", "--short=12", "HEAD")
    describe = _run_git(root, "describe", "--tags", "--always", "--dirty")
    dirty_output = _run_git(root, "status", "--porcelain", "--untracked-files=no")
    dirty = bool(dirty_output)

    source = "unknown"
    label = "unknown"
    if env_version:
        source = "env"
        label = env_version
    elif branch and commit_short:
        source = "git"
        label = f"{branch}@{commit_short}"
    elif describe:
        source = "git"
        label = describe
    elif commit_short:
        source = "git"
        label = commit_short

    return {
        "label": f"{label}*" if dirty and not label.endswith("*") else label,
        "source": source,
        "branch": branch,
        "commit": commit,
        "commit_short": commit_short,
        "describe": describe,
        "dirty": dirty,
        "app_root": str(root),
    }


def format_app_version_label(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("label") or "unknown")


def format_app_version_detail(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    source = str(snapshot.get("source") or "")
    branch = str(snapshot.get("branch") or "")
    commit_short = str(snapshot.get("commit_short") or "")

    if source:
        parts.append(source)
    if branch:
        parts.append(f"branch {branch}")
    if commit_short:
        parts.append(f"commit {commit_short}")
    parts.append("dirty" if snapshot.get("dirty") else "clean")
    return " | ".join(parts)
