# Bitkub Bot — Docker / Deploy Review Summary for Codex

## Overview
This project originally ran on a DigitalOcean Droplet using:
- Python directly on the server
- `.venv`
- `systemd` services:
  - `bitkub-engine.service`
  - `bitkub-streamlit.service`

It has now been migrated to Docker Compose.

Current runtime services:
- `bitkub-engine`
- `bitkub-streamlit`

Current UI URL:
- `http://<server-ip>:8501`

---

## What was done

### Dockerization
Added these files to the repo:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

### Compose runtime
Current Compose services:
- `engine` → `python main.py`
- `streamlit` → `python -m streamlit run ui/streamlit/app.py --server.port=8501 --server.address=0.0.0.0`

Current bind mount:
- `.:/app`

### Runtime state on server
The app is now running through Docker.

Checks already performed:
- `docker compose ps` shows:
  - `bitkub-engine`
  - `bitkub-streamlit`
- `bitkub-streamlit` publishes `0.0.0.0:8501->8501/tcp`
- `bitkub-engine.service` is inactive
- `bitkub-streamlit.service` is inactive
- port `8501` is owned by `docker-proxy`

Important interpretation:
- Seeing `python main.py`
- and `python -m streamlit ...`

in `ps aux` is normal when containers are running.
These are container processes visible from the host.

What should **not** appear anymore:
- `/opt/bitkub/Bitkub_Bot/.venv/bin/python ...`

---

## Git / branch work already done
A branch was created and merged:
- `chore/dockerize-bitkub-bot`

The Docker-related files were committed, pushed, and merged into `main`.

---

## What is being worked on now
The current focus is **deploy script / GitHub Actions**, not Docker runtime debugging.

Relevant files:
- `deploy/deploy_prod.sh`
- `.github/workflows/deploy.yml`

---

## Previous deploy model

### `deploy/deploy_prod.sh` (old behavior)
Old deploy flow was based on:
- virtualenv creation / usage
- `pip install -r requirements.txt`
- local Python smoke checks
- `systemctl restart bitkub-engine`
- `systemctl restart bitkub-streamlit`

This no longer matches the current Docker-based runtime.

### `deploy.yml` (old behavior)
The workflow already SSHs into the server and triggers `deploy/deploy_prod.sh`.
So the overall structure is still usable.
The main thing that needs to change is what the script does on the server.

---

## What Codex should review

### 1) Review `deploy/deploy_prod.sh`
Verify that the new version:
- removes old `.venv` / `pip install` / `systemctl restart` logic
- uses Docker-based deployment instead:
  - `git fetch / pull`
  - `docker compose build`
  - `docker compose up -d --remove-orphans`
  - `docker compose ps`
- handles failures safely
- behaves correctly when the server working tree is dirty

Important design choice to review:
Should deploy fail when the working tree is dirty, or should it force-sync the repo?

Two possible strategies:

#### Safe mode
Fail deployment if working tree is dirty.
This is safer for production.

#### Force-sync mode
Use something like:

```bash
git fetch origin main
git switch main
git reset --hard origin/main
git clean -fd
docker compose build
docker compose up -d --remove-orphans
```

This makes the server follow the repo exactly, but it discards local changes.

Codex should evaluate which model is more suitable.

---

### 2) Review `.github/workflows/deploy.yml`
Check whether the workflow:
- triggers correctly
- uses the existing GitHub secrets correctly
- SSHs into the server correctly
- calls `deploy/deploy_prod.sh` correctly
- still contains any old logic tied to `systemctl` or non-Docker deploy flow

Goal:
The workflow should be fully aligned with Docker Compose deployment.

---

### 3) Review `docker-compose.yml`
Check production suitability:
- is `build: .` acceptable for current use?
- is bind mount `.:/app` appropriate for production?
- should production instead mount only state/config paths such as:
  - `.env`
  - `config.json`
  - `runtime_state.json`
  - `data/`
  - `logs/`
- is `restart: unless-stopped` appropriate?
- is it correct that `engine` does not publish a port?
- is `streamlit` publishing `8501:8501` correctly?

---

### 4) Review `Dockerfile`
Check:
- whether `python:3.12-slim` is appropriate
- whether installed packages are sufficient
- whether layer ordering / caching is good enough
- whether the image is production-ready enough
- whether container processes should run as non-root user

Current note:
containers currently appear to run as `root`.
Codex should review whether that should be changed.

---

### 5) Review the effect of using `.:/app`
This is an important production concern.

Potential risks:
- local server changes override what is inside the image
- runtime behavior may differ from the built image
- dirty working tree complicates `git pull` and deploy consistency
- production deploy may become non-reproducible

Codex should evaluate whether production should move to:
- image-based deployment
- mount only config/state/data paths
- stop mounting the whole repo into the container

---

### 6) Review legacy systemd handling
Legacy services:
- `bitkub-engine.service`
- `bitkub-streamlit.service`

Status now:
- stopped / disabled
- runtime is now Docker
- there was earlier behavior where old Streamlit briefly came back

Codex should recommend which of these is best:
- keep them disabled only
- mask them
- delete / archive old service files
- update documentation to reflect Docker runtime instead of systemd runtime

---

### 7) Review server-side deployment strategy
Main question:
If local server changes should be ignored, should deploy use force-sync behavior like:

```bash
git fetch origin main
git switch main
git reset --hard origin/main
git clean -fd
docker compose build
docker compose up -d --remove-orphans
```

Or should deployment stop when local changes exist?

Codex should explain:
- pros/cons
- safety tradeoffs
- best production recommendation

---

## Important context for Codex
- Runtime is already Docker-based now
- Deployment must move from `systemd` restart flow to Docker Compose flow
- The main concern now is **deployment design**, not whether Docker runs
- The current production setup still uses `.:/app`, which may not be ideal long-term

---

## Ready-to-use prompt for Codex

Please review the files `deploy/deploy_prod.sh`, `.github/workflows/deploy.yml`, `Dockerfile`, and `docker-compose.yml` for the `Bitkub_Bot` project.

Context:
- The project originally ran on the server using `.venv` + `systemd`
- It has now been migrated to Docker Compose
- Current services are:
  - `engine` running `python main.py`
  - `streamlit` running `python -m streamlit run ui/streamlit/app.py --server.port=8501 --server.address=0.0.0.0`
- `streamlit` publishes `8501:8501`
- The current Compose setup still uses bind mount `.:/app`
- Deployment now needs to change from `pip install + systemctl restart` to `docker compose build + docker compose up -d --remove-orphans`

Please review:
- correctness
- production readiness
- security
- dirty working tree handling on the server
- whether force-sync or fail-on-dirty is the better deploy approach
- whether the bind mount strategy should be changed for production
- whether the containers should stop running as root

Also identify any remaining problems or risks in the Docker deployment model.
