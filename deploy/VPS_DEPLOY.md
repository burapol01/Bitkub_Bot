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

The deploy script exports the host user UID/GID to keep the container writeable without running as root.
If the SSH user is `root`, it falls back to a non-root container identity and fixes ownership on `runtime/` and `data/`.

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

Caddy is now integrated into the Docker Compose stack as the public entry point. The setup is as follows:

- **Public access:** Caddy listens on ports 80/443 and is exposed to the host.
- **Internal access:** Streamlit only exposes port 8501 internally within the Docker network (not directly to the host).
- **Configuration:** The Caddyfile is at `deploy/Caddyfile` and can be edited to change the site address or enable HTTPS with a real domain.

Currently configured for temporary testing at `http://165.22.108.218`. When you obtain a domain, update the site address in `deploy/Caddyfile` and Caddy will automatically provision HTTPS certificates via ACME.

## Archived Legacy Files

The old `bitkub-engine.service` and `bitkub-streamlit.service` files now live in `deploy/archive/systemd/` for reference only. They are not part of the active Docker deployment path.
