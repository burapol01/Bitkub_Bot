#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${BITKUB_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_PATH="${BITKUB_VENV_PATH:-$APP_ROOT/.venv}"
DEPLOY_REMOTE="${BITKUB_DEPLOY_REMOTE:-origin}"
DEPLOY_BRANCH="${BITKUB_DEPLOY_BRANCH:-main}"
DEPLOY_EXPECT_COMMIT="${BITKUB_DEPLOY_EXPECT_COMMIT:-}"
DEPLOY_PYTHON_BIN="${BITKUB_DEPLOY_PYTHON_BIN:-python3}"
DEPLOY_UPGRADE_PIP="${BITKUB_DEPLOY_UPGRADE_PIP:-0}"
DEPLOY_RESTART_STREAMLIT="${BITKUB_DEPLOY_RESTART_STREAMLIT:-1}"
ENGINE_SERVICE="${BITKUB_ENGINE_SERVICE:-bitkub-engine}"
STREAMLIT_SERVICE="${BITKUB_STREAMLIT_SERVICE:-bitkub-streamlit}"
VERSION_FILE="${BITKUB_APP_VERSION_FILE:-$APP_ROOT/.bitkub-app-version.json}"
RUNTIME_USER="${BITKUB_RUNTIME_USER:-bitkub}"
RUNTIME_GROUP="${BITKUB_RUNTIME_GROUP:-$RUNTIME_USER}"

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

run_systemctl() {
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo -n systemctl "$@"
  else
    fail "systemctl requires root or passwordless sudo"
  fi
}

ensure_runtime_ownership() {
  if [[ "$(id -u)" -ne 0 ]]; then
    local current_user
    current_user="$(id -un)"
    if [[ "$current_user" != "$RUNTIME_USER" ]]; then
      log "Deploy is running as $current_user while services run as $RUNTIME_USER; prefer using the runtime user for SSH deploys"
    fi
    return
  fi

  if ! id "$RUNTIME_USER" >/dev/null 2>&1; then
    log "Skipping ownership normalization because runtime user $RUNTIME_USER does not exist"
    return
  fi

  log "Normalizing ownership to $RUNTIME_USER:$RUNTIME_GROUP for $APP_ROOT"
  chown -R "$RUNTIME_USER:$RUNTIME_GROUP" "$APP_ROOT"
}

switch_branch() {
  if git show-ref --verify --quiet "refs/heads/$DEPLOY_BRANCH"; then
    git switch "$DEPLOY_BRANCH"
  else
    git switch -c "$DEPLOY_BRANCH" --track "$DEPLOY_REMOTE/$DEPLOY_BRANCH"
  fi
}

ensure_virtualenv() {
  if [[ -x "$VENV_PATH/bin/python" ]]; then
    return
  fi

  log "Creating virtualenv at $VENV_PATH"
  "$DEPLOY_PYTHON_BIN" -m venv "$VENV_PATH"
}

write_version_metadata() {
  local version_branch="$1"
  local version_commit="$2"
  local version_commit_short="$3"

  log "Writing deploy version metadata to $VERSION_FILE"
  BITKUB_WRITE_VERSION_FILE="$VERSION_FILE" \
  BITKUB_WRITE_VERSION_BRANCH="$version_branch" \
  BITKUB_WRITE_VERSION_COMMIT="$version_commit" \
  BITKUB_WRITE_VERSION_COMMIT_SHORT="$version_commit_short" \
  "$VENV_PATH/bin/python" -B - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

payload = {
    "source": "deploy",
    "label": f"{os.environ['BITKUB_WRITE_VERSION_BRANCH']}@{os.environ['BITKUB_WRITE_VERSION_COMMIT_SHORT']}",
    "branch": os.environ["BITKUB_WRITE_VERSION_BRANCH"],
    "commit": os.environ["BITKUB_WRITE_VERSION_COMMIT"],
    "commit_short": os.environ["BITKUB_WRITE_VERSION_COMMIT_SHORT"],
    "dirty": False,
    "deployed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}

path = Path(os.environ["BITKUB_WRITE_VERSION_FILE"])
path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
PY
  chmod 644 "$VERSION_FILE"
}

run_smoke_checks() {
  log "Running config validation"
  "$VENV_PATH/bin/python" -B - <<'PY'
from config import reload_config

config, errors = reload_config()
if config is None:
    raise SystemExit("Config validation failed: " + "; ".join(errors))

print("Config validation ok")
PY

  log "Running compile smoke check"
  "$VENV_PATH/bin/python" -B - <<'PY'
from pathlib import Path

files = [
    "main.py",
    "ui/streamlit/app.py",
    "services/version_service.py",
]
for path in files:
    compile(Path(path).read_text(encoding="utf-8"), path, "exec")

print("Compile smoke check ok")
PY
}

restart_services() {
  log "Reloading systemd units"
  run_systemctl daemon-reload

  log "Restarting $ENGINE_SERVICE"
  run_systemctl restart "$ENGINE_SERVICE"
  run_systemctl is-active --quiet "$ENGINE_SERVICE" || fail "$ENGINE_SERVICE is not active after restart"

  if [[ "$DEPLOY_RESTART_STREAMLIT" == "1" ]]; then
    log "Restarting $STREAMLIT_SERVICE"
    run_systemctl restart "$STREAMLIT_SERVICE"
    run_systemctl is-active --quiet "$STREAMLIT_SERVICE" || fail "$STREAMLIT_SERVICE is not active after restart"
  else
    log "Skipping Streamlit restart because BITKUB_DEPLOY_RESTART_STREAMLIT=$DEPLOY_RESTART_STREAMLIT"
  fi
}

main() {
  umask 027

  require_command git
  require_command "$DEPLOY_PYTHON_BIN"

  [[ -d "$APP_ROOT" ]] || fail "APP_ROOT does not exist: $APP_ROOT"
  cd "$APP_ROOT"

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "APP_ROOT is not a git repository"

  log "Deploying $DEPLOY_BRANCH from $DEPLOY_REMOTE inside $APP_ROOT"
  git fetch "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"
  switch_branch
  git pull --ff-only "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"

  local current_commit
  current_commit="$(git rev-parse HEAD)"
  local current_short
  current_short="$(git rev-parse --short=12 HEAD)"
  log "Checked out commit $current_short"

  if [[ -n "$DEPLOY_EXPECT_COMMIT" && "$current_commit" != "$DEPLOY_EXPECT_COMMIT" ]]; then
    fail "Expected commit $DEPLOY_EXPECT_COMMIT but deployed $current_commit"
  fi

  ensure_virtualenv

  if [[ "$DEPLOY_UPGRADE_PIP" == "1" ]]; then
    log "Upgrading pip"
    "$VENV_PATH/bin/python" -m pip install --upgrade pip
  fi

  log "Installing Python dependencies"
  "$VENV_PATH/bin/python" -m pip install -r requirements.txt

  write_version_metadata "$DEPLOY_BRANCH" "$current_commit" "$current_short"
  ensure_runtime_ownership
  run_smoke_checks
  restart_services

  log "Deploy completed successfully at ${DEPLOY_BRANCH}@${current_short}"
}

main "$@"
