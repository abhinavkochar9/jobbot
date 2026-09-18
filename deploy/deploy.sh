#!/usr/bin/env bash
# Deploy jobbot to the umkc server (~/jobbot). Run from the project root
# on the laptop:  JOBBOT_HOST=<ssh-alias> bash deploy/deploy.sh
set -euo pipefail

HOST="${JOBBOT_HOST:-myserver}"   # SSH host alias for your server
DEST='~/jobbot'

echo "==> rsync project"
rsync -az --delete \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude data \
  --exclude logs --exclude materials --exclude evidence --exclude reports \
  --exclude .env \
  ./ "$HOST:jobbot/"

echo "==> remote setup"
ssh "$HOST" bash -s <<'REMOTE'
set -euo pipefail
cd ~/jobbot
mkdir -p data logs materials evidence reports
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m playwright install chromium 2>&1 | tail -1 || true
# Playwright OS deps need root; if chromium fails to launch, run:
#   sudo ~/jobbot/.venv/bin/python -m playwright install-deps chromium
if [ ! -f .env ]; then cp .env.example .env; echo "!! Fill in ~/jobbot/.env"; fi
mkdir -p ~/.config/systemd/user
cp deploy/jobbot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable jobbot
loginctl enable-linger "$USER" 2>/dev/null || echo "!! run: loginctl enable-linger $USER (may need admin)"
echo "==> deployed. start with: systemctl --user start jobbot"
REMOTE
