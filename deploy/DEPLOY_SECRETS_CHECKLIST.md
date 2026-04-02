# GitHub Actions SSH Deploy Checklist

This project now supports a simple production deploy flow:

1. Push to `main`
2. GitHub Actions opens an SSH session to the DigitalOcean droplet
3. The runner calls `deploy/deploy_prod.sh`
4. The droplet pulls the latest code, installs dependencies, runs smoke checks, and restarts `bitkub-engine` plus `bitkub-streamlit`

## 1. Server Prerequisites

- Droplet is reachable from GitHub Actions over SSH
- App lives at `/opt/bitkub/Bitkub_Bot`
- The deploy user can run:
  - `git fetch origin main`
  - `systemctl restart bitkub-engine`
  - `systemctl restart bitkub-streamlit`
- `.env`, `config.base.json`, `config.prod.override.json`, `data/*.db`, and `runtime_state.json` stay on the server
- `bitkub-engine` and `bitkub-streamlit` systemd services are already installed

## 2. Create A Dedicated SSH Key Pair

Run this on your local machine:

```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/bitkub_github_actions
```

You will get:

- private key: `~/.ssh/bitkub_github_actions`
- public key: `~/.ssh/bitkub_github_actions.pub`

Add the public key to the deploy user's `authorized_keys` on the droplet:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat ~/bitkub_github_actions.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Test it:

```bash
ssh -i ~/.ssh/bitkub_github_actions bitkub@YOUR_DROPLET_IP
```

## 3. Add GitHub Secrets

In GitHub:

- `Settings`
- `Secrets and variables`
- `Actions`
- `New repository secret`

Create these secrets:

- `DO_SSH_HOST`
  - example: `203.0.113.10`
- `DO_SSH_PORT`
  - example: `22`
- `DO_SSH_USER`
  - example: `bitkub`
- `DO_SSH_PRIVATE_KEY`
  - paste the full private key from `~/.ssh/bitkub_github_actions`
- `DO_SSH_KNOWN_HOSTS`
  - generate with:

```bash
ssh-keyscan -H YOUR_DROPLET_IP
```

Paste the full output into the secret.

## 4. Make Sure The Server Repo Can Pull From GitHub

The deploy script runs `git fetch origin main` on the droplet.

That means the droplet itself must already be able to read the repository.

If the repository is private, configure one of these on the server:

- a read-only deploy key
- a machine user with read access
- a GitHub token already embedded in the remote URL

Test directly on the droplet:

```bash
cd /opt/bitkub/Bitkub_Bot
git fetch origin main
```

If this fails, GitHub Actions will SSH in successfully but deploy will still stop at the `git fetch` step.

## 5. Allow Service Restart Commands

If the deploy user is not root, allow passwordless `systemctl` for the required commands.

Example on Ubuntu/DigitalOcean:

```bash
sudo visudo -f /etc/sudoers.d/bitkub-deploy
```

Add:

```text
bitkub ALL=NOPASSWD: /usr/bin/systemctl daemon-reload, /usr/bin/systemctl restart bitkub-engine, /usr/bin/systemctl restart bitkub-streamlit, /usr/bin/systemctl is-active --quiet bitkub-engine, /usr/bin/systemctl is-active --quiet bitkub-streamlit
```

Then test:

```bash
sudo -n systemctl daemon-reload
sudo -n systemctl restart bitkub-engine
sudo -n systemctl restart bitkub-streamlit
sudo -n systemctl is-active --quiet bitkub-engine
sudo -n systemctl is-active --quiet bitkub-streamlit
```

## 6. First Manual Server Test

Before trusting GitHub Actions, SSH into the droplet and run:

```bash
cd /opt/bitkub/Bitkub_Bot
bash deploy/deploy_prod.sh
```

If that works manually, the GitHub Actions workflow should work too.

## 7. Recommended Rollout Order

- run `deploy/deploy_prod.sh` manually once on the droplet
- confirm both services restart cleanly
- push a tiny commit to `main`
- watch the GitHub Actions run
- confirm the app version shown in UI and engine logs matches the new commit

## 8. Notes

- This flow is intentionally simple and good for a single DigitalOcean droplet
- Do not let the deploy pipeline overwrite server-owned files such as `.env`, prod override config, SQLite DBs, or runtime state
- If you later need zero-downtime deploys, release artifacts, or rollback slots, you can evolve this flow after the simple path is stable
