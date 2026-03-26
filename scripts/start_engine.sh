#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${BITKUB_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_PATH="${BITKUB_VENV_PATH:-$APP_ROOT/.venv}"

cd "$APP_ROOT"
exec "$VENV_PATH/bin/python" main.py
