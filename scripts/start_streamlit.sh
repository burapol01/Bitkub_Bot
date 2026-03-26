#!/usr/bin/env bash
set -euo pipefail

APP_ROOT="${BITKUB_APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
VENV_PATH="${BITKUB_VENV_PATH:-$APP_ROOT/.venv}"
STREAMLIT_PORT="${STREAMLIT_SERVER_PORT:-8501}"
STREAMLIT_ADDRESS="${STREAMLIT_SERVER_ADDRESS:-0.0.0.0}"

cd "$APP_ROOT"
exec "$VENV_PATH/bin/python" -m streamlit run ui/streamlit/app.py \
  --server.port "$STREAMLIT_PORT" \
  --server.address "$STREAMLIT_ADDRESS"
