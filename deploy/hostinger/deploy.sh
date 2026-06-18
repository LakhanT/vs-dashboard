#!/usr/bin/env bash
# Update VS Dashboard on VPS after git push.
# Run as root: bash /opt/vs-dashboard/deploy/hostinger/deploy.sh
set -euo pipefail

APP_DIR="/opt/vs-dashboard"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo bash deploy.sh)"
  exit 1
fi

git -C "$APP_DIR" pull --ff-only origin main

"$APP_DIR/backend/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

cd "$APP_DIR/frontend"
npm ci
npm run build

chown -R www-data:www-data "$APP_DIR/frontend/dist" "$APP_DIR/backend/data" 2>/dev/null || true

systemctl restart vs-dashboard-api
systemctl reload nginx

echo "Deploy complete — $(date -Iseconds)"
