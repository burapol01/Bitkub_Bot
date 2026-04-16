# VPS Deploy

This project now runs on a single Linux VPS with Docker Compose.

## Recommended layout

- App root: `/opt/bitkub/Bitkub_Bot`
- Mutable runtime files: `/opt/bitkub/Bitkub_Bot/runtime`
- SQLite and other persisted data: `/opt/bitkub/Bitkub_Bot/data`
- Server secrets: `/opt/bitkub/Bitkub_Bot/.env`

## Runtime paths

The containers use these paths:

- `BITKUB_APP_ROOT=/app`
- `BITKUB_CONFIG_BASE_PATH=/app/config.base.json`
- `BITKUB_CONFIG_PATH=/app/runtime/config.json`
- `BITKUB_DB_PATH=/app/data/bitkub.db`
- `BITKUB_RUNTIME_STATE_PATH=/app/runtime/runtime_state.json`
- `BITKUB_SIGNAL_LOG_FILE=/app/runtime/signal_log.csv`
- `BITKUB_TRADE_LOG_FILE=/app/runtime/paper_trade_log.csv`

The deploy script also exports the host user UID/GID to keep the container writeable without running as root.

## First-time setup

```bash
sudo adduser --disabled-password --gecos "" bitkub
sudo mkdir -p /opt/bitkub
sudo chown -R bitkub:bitkub /opt/bitkub
```

Clone the repository into `/opt/bitkub/Bitkub_Bot`, then as the `bitkub` user:

```bash
cd /opt/bitkub/Bitkub_Bot
mkdir -p runtime data
chmod +x deploy/deploy_prod.sh
```

Put your production secrets in `/opt/bitkub/Bitkub_Bot/.env`.

## Run and deploy

Use the deploy script to sync the branch, seed the runtime config if needed, build the images, and bring the containers up:

```bash
bash deploy/deploy_prod.sh
```

After a successful deploy:

```bash
docker compose ps
docker compose logs -f engine
docker compose logs -f streamlit
```

If you want a one-off manual refresh without waiting for GitHub Actions, rerun the same deploy script on the VPS.

## Update flow

```bash
bash deploy/deploy_prod.sh
```

The script fails fast if the working tree has local tracked or untracked changes. That keeps production from drifting away from the repo.

## Reverse proxy

If you want browser access from outside the VPS, put Nginx or Caddy in front of Streamlit and expose only port 8501 internally.

## Legacy systemd

The old `bitkub-engine.service` and `bitkub-streamlit.service` files are now legacy only. Keep them archived or disabled, but do not use them for the Docker deployment path.
