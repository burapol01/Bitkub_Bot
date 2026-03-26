# VPS Deploy

This project can run both the engine and Streamlit UI on a single Linux VPS.

## Recommended layout

- App root: `/opt/bitkub/Bitkub_Bot`
- Python venv: `/opt/bitkub/Bitkub_Bot/.venv`
- Config: `/opt/bitkub/Bitkub_Bot/config.json`
- SQLite: `/opt/bitkub/Bitkub_Bot/data/bitkub.db`
- Runtime state: `/opt/bitkub/Bitkub_Bot/runtime_state.json`

## Environment variables

These paths can now be overridden:

- `BITKUB_CONFIG_PATH`
- `BITKUB_DB_PATH`
- `BITKUB_RUNTIME_STATE_PATH`
- `BITKUB_APP_ROOT`
- `BITKUB_VENV_PATH`
- `STREAMLIT_SERVER_PORT`
- `STREAMLIT_SERVER_ADDRESS`

The existing secrets in `.env` still work:

- `BITKUB_API_KEY`
- `BITKUB_API_SECRET`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID` or `TELEGRAM_CHAT_IDS`
- `TELEGRAM_ALLOWED_CHAT_IDS`

## First-time setup

```bash
sudo adduser --disabled-password --gecos "" bitkub
sudo mkdir -p /opt/bitkub
sudo chown -R bitkub:bitkub /opt/bitkub
```

Copy the project into `/opt/bitkub/Bitkub_Bot`, then as the `bitkub` user:

```bash
cd /opt/bitkub/Bitkub_Bot
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
mkdir -p data
chmod +x scripts/start_engine.sh scripts/start_streamlit.sh
```

## Run manually

Engine:

```bash
BITKUB_APP_ROOT=/opt/bitkub/Bitkub_Bot BITKUB_VENV_PATH=/opt/bitkub/Bitkub_Bot/.venv ./scripts/start_engine.sh
```

Streamlit:

```bash
BITKUB_APP_ROOT=/opt/bitkub/Bitkub_Bot BITKUB_VENV_PATH=/opt/bitkub/Bitkub_Bot/.venv ./scripts/start_streamlit.sh
```

## systemd services

Copy the templates from `deploy/systemd/`:

```bash
sudo cp deploy/systemd/bitkub-engine.service /etc/systemd/system/
sudo cp deploy/systemd/bitkub-streamlit.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bitkub-engine
sudo systemctl enable --now bitkub-streamlit
```

Check status:

```bash
sudo systemctl status bitkub-engine
sudo systemctl status bitkub-streamlit
```

View logs:

```bash
journalctl -u bitkub-engine -f
journalctl -u bitkub-streamlit -f
```

## Update flow

```bash
cd /opt/bitkub/Bitkub_Bot
git pull
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart bitkub-engine
sudo systemctl restart bitkub-streamlit
```

## Reverse proxy

If you want browser access from outside the VPS, put Nginx or Caddy in front of Streamlit and expose only the UI service.

## Current Linux note

`main.py` now falls back safely when Windows-only `msvcrt` is unavailable. On Linux/systemd, the engine keeps running but interactive console hotkeys are not available.
