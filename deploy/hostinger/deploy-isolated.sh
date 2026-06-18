#!/usr/bin/env bash
# Update VS Dashboard on shared VPS (isolated install).
set -euo pipefail

APP_DIR="${VS_DASHBOARD_DIR:-$HOME/vs-dashboard}"

git -C "$APP_DIR" pull --ff-only origin main
"$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"

cd "$APP_DIR/frontend"
npm ci --silent
npm run build

sudo systemctl restart vs-dashboard-api
echo "Updated $(date -Iseconds) — API restarted, nginx static files refreshed."
