from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.env_service import APP_ROOT

GIT_TIMEOUT_SECONDS = 2.0
DEFAULT_VERSION_FILE_NAME = ".bitkub-app-version.json"


def _app_root() -> Path:
    raw_root = os.getenv("BITKUB_APP_ROOT")
    if raw_root:
        return Path(raw_root).expanduser().resolve()
    return APP_ROOT.resolve()


def _version_file_path(root: Path) -> Path:
    raw_path = str(os.getenv("BITKUB_APP_VERSION_FILE") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    return root / DEFAULT_VERSION_FILE_NAME


def _git_executable() -> str | None:
    resolved = shutil.which("git")
    if resolved:
        return resolved
    for candidate in ("/usr/bin/git", "/usr/local/bin/git"):
        if Path(candidate).is_file():
            return candidate
    return None


def _run_git(root: Path, *args: str) -> str | None:
    git_executable = _git_executable()
    if not git_executable:
        return None

    try:
        completed = subprocess.run(
            [git_executable, "-C", str(root), *args],
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


def _read_version_file(path: Path) -> dict[str, Any]:
    try:
        raw_text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}

    if not raw_text:
        return {}

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return {"label": raw_text, "source": "file"}

    if not isinstance(payload, dict):
        return {}

    snapshot: dict[str, Any] = {}
    for key in ("label", "source", "branch", "commit", "commit_short", "describe"):
        value = str(payload.get(key) or "").strip()
        if value:
            snapshot[key] = value
    snapshot["dirty"] = bool(payload.get("dirty"))
    return snapshot


@lru_cache(maxsize=1)
def get_app_version_snapshot() -> dict[str, Any]:
    root = _app_root()
    version_file = _version_file_path(root)
    env_version = str(os.getenv("BITKUB_APP_VERSION") or "").strip()

    git_branch = _run_git(root, "branch", "--show-current")
    git_commit = _run_git(root, "rev-parse", "HEAD")
    git_commit_short = _run_git(root, "rev-parse", "--short=12", "HEAD")
    git_describe = _run_git(root, "describe", "--tags", "--always", "--dirty")
    dirty_output = _run_git(root, "status", "--porcelain", "--untracked-files=no")
    file_snapshot = _read_version_file(version_file)

    branch = git_branch or str(file_snapshot.get("branch") or "").strip() or None
    commit = git_commit or str(file_snapshot.get("commit") or "").strip() or None
    commit_short = (
        git_commit_short or str(file_snapshot.get("commit_short") or "").strip() or None
    )
    describe = git_describe or str(file_snapshot.get("describe") or "").strip() or None
    dirty = bool(dirty_output) or bool(file_snapshot.get("dirty"))

    source = "unknown"
    label = "unknown"
    if env_version:
        source = "env"
        label = env_version
    elif git_branch and git_commit_short:
        source = "git"
        label = f"{git_branch}@{git_commit_short}"
    elif git_describe:
        source = "git"
        label = git_describe
    elif git_commit_short:
        source = "git"
        label = git_commit_short
    else:
        file_source = str(file_snapshot.get("source") or "").strip() or "file"
        file_label = str(file_snapshot.get("label") or "").strip()
        if file_label:
            source = file_source
            label = file_label
        elif branch and commit_short:
            source = file_source
            label = f"{branch}@{commit_short}"
        elif describe:
            source = file_source
            label = describe
        elif commit_short:
            source = file_source
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
        "version_file": str(version_file),
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
