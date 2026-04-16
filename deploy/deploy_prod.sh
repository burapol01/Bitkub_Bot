#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${BITKUB_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DEPLOY_REMOTE="${BITKUB_DEPLOY_REMOTE:-origin}"
DEPLOY_BRANCH="${BITKUB_DEPLOY_BRANCH:-main}"
DEPLOY_EXPECT_COMMIT="${BITKUB_DEPLOY_EXPECT_COMMIT:-}"
VERSION_FILE="${BITKUB_APP_VERSION_FILE:-$APP_ROOT/.bitkub-app-version.json}"
RUNTIME_DIR="${BITKUB_RUNTIME_DIR:-$APP_ROOT/runtime}"
RUNTIME_CONFIG_FILE="${BITKUB_RUNTIME_CONFIG_FILE:-$RUNTIME_DIR/config.json}"

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

require_docker_compose() {
  docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin is required"
}

switch_branch() {
  if git show-ref --verify --quiet "refs/heads/$DEPLOY_BRANCH"; then
    git switch "$DEPLOY_BRANCH"
  else
    git switch -c "$DEPLOY_BRANCH" --track "$DEPLOY_REMOTE/$DEPLOY_BRANCH"
  fi
}

ensure_clean_worktree() {
  local status_output
  status_output="$(git status --porcelain=v1 --untracked-files=normal)"

  if [[ -n "$status_output" ]]; then
    log "Working tree is dirty:"
    printf '%s\n' "$status_output"
    fail "Refusing to deploy with local tracked or untracked changes"
  fi
}

ensure_branch_fast_forward_only() {
  local ahead_count
  ahead_count="$(git rev-list --count "$DEPLOY_REMOTE/$DEPLOY_BRANCH..HEAD")"
  if [[ "$ahead_count" != "0" ]]; then
    fail "Local branch $DEPLOY_BRANCH has $ahead_count commit(s) not present on $DEPLOY_REMOTE/$DEPLOY_BRANCH"
  fi
}

ensure_runtime_layout() {
  mkdir -p "$RUNTIME_DIR" "$APP_ROOT/data"
}

seed_runtime_config() {
  if [[ -e "$RUNTIME_CONFIG_FILE" ]]; then
    return
  fi

  [[ -f "$APP_ROOT/config.json" ]] || fail "Missing source config file: $APP_ROOT/config.json"

  log "Seeding runtime config at $RUNTIME_CONFIG_FILE"
  cp "$APP_ROOT/config.json" "$RUNTIME_CONFIG_FILE"
}

write_version_metadata() {
  local version_branch="$1"
  local version_commit="$2"
  local version_commit_short="$3"

  log "Writing deploy version metadata to $VERSION_FILE"
  cat > "$VERSION_FILE" <<EOF
{
  "source": "deploy",
  "label": "${version_branch}@${version_commit_short}",
  "branch": "${version_branch}",
  "commit": "${version_commit}",
  "commit_short": "${version_commit_short}",
  "dirty": false,
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
  chmod 644 "$VERSION_FILE"
}

main() {
  umask 027

  require_command git
  require_command docker
  require_docker_compose

  [[ -d "$APP_ROOT" ]] || fail "APP_ROOT does not exist: $APP_ROOT"
  cd "$APP_ROOT"
  export DOCKER_UID="$(id -u)"
  export DOCKER_GID="$(id -g)"

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "APP_ROOT is not a git repository"

  log "Deploying $DEPLOY_BRANCH from $DEPLOY_REMOTE inside $APP_ROOT"

  ensure_clean_worktree

  git fetch "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"
  switch_branch
  git pull --ff-only "$DEPLOY_REMOTE" "$DEPLOY_BRANCH"
  ensure_branch_fast_forward_only

  local current_commit
  current_commit="$(git rev-parse HEAD)"
  local current_short
  current_short="$(git rev-parse --short=12 HEAD)"
  log "Checked out commit $current_short"

  if [[ -n "$DEPLOY_EXPECT_COMMIT" && "$current_commit" != "$DEPLOY_EXPECT_COMMIT" ]]; then
    fail "Expected commit $DEPLOY_EXPECT_COMMIT but deployed $current_commit"
  fi

  ensure_runtime_layout
  seed_runtime_config
  write_version_metadata "$DEPLOY_BRANCH" "$current_commit" "$current_short"

  log "Building Docker images"
  docker compose build

  log "Starting Docker services"
  docker compose up -d --remove-orphans

  log "Current Docker services"
  docker compose ps

  log "Deploy completed successfully at ${DEPLOY_BRANCH}@${current_short}"
}

main "$@"
