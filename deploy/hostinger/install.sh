#!/usr/bin/env bash
# One-time Hostinger VPS setup for VS Dashboard (Ubuntu 22.04 / 24.04).
# Run as root: bash install.sh YOUR_DOMAIN your@email.com
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
APP_DIR="/opt/vs-dashboard"
REPO="${VS_DASHBOARD_REPO:-https://github.com/LakhanT/vs-dashboard.git}"

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  echo "Usage: sudo bash install.sh YOUR_DOMAIN your@email.com"
  echo "Example: sudo bash install.sh dashboard.example.com admin@example.com"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root (sudo bash install.sh ...)"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y git nginx certbot python3-certbot-nginx \
  python3 python3-venv python3-pip curl ca-certificates

# Node.js 20 for frontend build
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

mkdir -p "$APP_DIR"
if [[ ! -d "$APP_DIR/.git" ]]; then
  git clone "$REPO" "$APP_DIR"
else
  git -C "$APP_DIR" pull --ff-only origin main || true
fi

mkdir -p "$APP_DIR/backend/data"
touch "$APP_DIR/backend/data/.keep"
chown -R www-data:www-data "$APP_DIR/backend/data"

# Backend venv + deps
python3 -m venv "$APP_DIR/backend/.venv"
"$APP_DIR/backend/.venv/bin/pip" install --upgrade pip
"$APP_DIR/backend/.venv/bin/pip" install -r "$APP_DIR/backend/requirements.txt"

# Production .env
if [[ ! -f "$APP_DIR/backend/.env" ]]; then
  cp "$APP_DIR/deploy/hostinger/env.production.example" "$APP_DIR/backend/.env"
  sed -i "s|YOUR_DOMAIN|${DOMAIN}|g" "$APP_DIR/backend/.env"
fi

# Frontend — same-origin /api (no VITE_API_BASE_URL needed)
cd "$APP_DIR/frontend"
npm ci
npm run build

chown -R www-data:www-data "$APP_DIR/frontend/dist" "$APP_DIR/backend"

# nginx
sed "s|YOUR_DOMAIN|${DOMAIN}|g" \
  "$APP_DIR/deploy/hostinger/nginx-vs-dashboard.conf.template" \
  > /etc/nginx/sites-available/vs-dashboard
ln -sf /etc/nginx/sites-available/vs-dashboard /etc/nginx/sites-enabled/vs-dashboard
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

# systemd API service
cp "$APP_DIR/deploy/hostinger/vs-dashboard-api.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable vs-dashboard-api
systemctl restart vs-dashboard-api

# HTTPS
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect || {
  echo "Certbot failed — check DNS points to this VPS, then run:"
  echo "  certbot --nginx -d $DOMAIN"
}

echo ""
echo "Done! Open: https://${DOMAIN}"
echo "API health: https://${DOMAIN}/api/health"
echo "Logs: journalctl -u vs-dashboard-api -f"
