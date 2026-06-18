#!/usr/bin/env bash
# Isolated VS Dashboard install for a VPS that already hosts other sites.
# Does NOT remove default nginx sites or change existing configs.
#
# Usage (on VPS as user lakhan):
#   bash deploy/hostinger/install-isolated.sh SUBDOMAIN.FULLDOMAIN.COM [API_PORT]
#
# Example:
#   bash deploy/hostinger/install-isolated.sh dashboard.yourdomain.com 8010
#
# Requires sudo only for: nginx site enable + systemd service (optional skip with --no-systemd)
set -euo pipefail

DOMAIN="${1:-}"
API_PORT="${2:-8010}"
APP_DIR="${VS_DASHBOARD_DIR:-$HOME/vs-dashboard}"
REPO="${VS_DASHBOARD_REPO:-https://github.com/LakhanT/vs-dashboard.git}"
USE_SYSTEMD=1
USE_NGINX=1

for arg in "$@"; do
  case "$arg" in
    --no-systemd) USE_SYSTEMD=0 ;;
    --no-nginx) USE_NGINX=0 ;;
  esac
done

if [[ -z "$DOMAIN" ]]; then
  echo "Usage: bash install-isolated.sh YOUR_SUBDOMAIN.yourdomain.com [API_PORT]"
  echo "  Use a dedicated subdomain so existing websites are untouched."
  exit 1
fi

echo "=== VS Dashboard isolated install ==="
echo "Domain:    $DOMAIN"
echo "App dir:   $APP_DIR"
echo "API port:  $API_PORT (localhost only)"
echo ""

# --- Preflight: do not clobber existing install ---
if ss -tln 2>/dev/null | grep -q ":${API_PORT} "; then
  echo "ERROR: Port $API_PORT is already in use. Pick another port:"
  echo "  bash install-isolated.sh $DOMAIN 8011"
  exit 1
fi

if [[ -d "$APP_DIR/.git" ]]; then
  echo "Updating existing clone at $APP_DIR"
  git -C "$APP_DIR" pull --ff-only origin main
else
  git clone "$REPO" "$APP_DIR"
fi

mkdir -p "$APP_DIR/backend/data"

# --- Python venv (user-local, no system Python changes) ---
if [[ ! -d "$APP_DIR/backend/.venv" ]]; then
  python3 -m venv "$APP_DIR/backend/.venv"
fi
"$APP_DIR/backend/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"

# --- .env (only create if missing) ---
ENV_FILE="$APP_DIR/backend/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  DB_PATH="${APP_DIR}/backend/data/vs_dashboard.db"
  cat > "$ENV_FILE" <<EOF
DATABASE_URL=sqlite:////${DB_PATH}

OHLC_DATA_SOURCE=yahoo
LIVE_PRICE_SOURCE=fyers
LIVE_PRICE_ENABLED=true
LIVE_PRICE_INTERVAL_SEC=15
LIVE_PRICE_BATCH_SIZE=100

CORS_ORIGINS=https://${DOMAIN},http://${DOMAIN}

FYERS_CREDENTIALS_FILE=${APP_DIR}/backend/credentials.txt
FYERS_REDIRECT_URI=http://127.0.0.1:5000/callback

CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/1
EOF
  echo "Created $ENV_FILE"
else
  echo "Keeping existing $ENV_FILE"
fi

# Ensure CORS includes this domain
if ! grep -q "$DOMAIN" "$ENV_FILE"; then
  echo "Add to CORS_ORIGINS in $ENV_FILE: https://${DOMAIN}"
fi

# --- Frontend build (needs node on PATH) ---
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found. Install Node 20+ or run: curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs"
  exit 1
fi
cd "$APP_DIR/frontend"
npm ci --silent
npm run build

echo ""
echo "=== Existing nginx sites (unchanged) ==="
ls -la /etc/nginx/sites-enabled/ 2>/dev/null || true
echo ""

# --- nginx: ADD only a new site file ---
if [[ "$USE_NGINX" -eq 1 ]]; then
  NGINX_AVAIL="/etc/nginx/sites-available/vs-dashboard"
  NGINX_ENABLED="/etc/nginx/sites-enabled/vs-dashboard"

  sudo tee "$NGINX_AVAIL" > /dev/null <<EOF
# VS Dashboard — isolated vhost (does not affect other sites)
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    root ${APP_DIR}/frontend/dist;
    index index.html;

    client_max_body_size 50M;

    location /api/ {
        proxy_pass http://127.0.0.1:${API_PORT}/api/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }
}
EOF

  sudo ln -sf "$NGINX_AVAIL" "$NGINX_ENABLED"
  sudo nginx -t
  sudo systemctl reload nginx
  echo "nginx: added $NGINX_ENABLED (other sites untouched)"
fi

# --- systemd: dedicated service only ---
if [[ "$USE_SYSTEMD" -eq 1 ]]; then
  sudo tee /etc/systemd/system/vs-dashboard-api.service > /dev/null <<EOF
[Unit]
Description=VS Dashboard API (isolated)
After=network.target

[Service]
Type=simple
User=${USER}
Group=${USER}
WorkingDirectory=${APP_DIR}/backend
EnvironmentFile=${ENV_FILE}
ExecStart=${APP_DIR}/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable vs-dashboard-api
  sudo systemctl restart vs-dashboard-api
  echo "systemd: vs-dashboard-api on 127.0.0.1:${API_PORT}"
fi

echo ""
echo "=== Done ==="
echo "1. Point DNS A record: ${DOMAIN} -> this server IP"
echo "2. SSL (after DNS works):"
echo "     sudo certbot --nginx -d ${DOMAIN}"
echo "3. Open: http://${DOMAIN}  (https after certbot)"
echo "4. Health: curl -s http://127.0.0.1:${API_PORT}/api/health"
echo "5. Logs:   journalctl -u vs-dashboard-api -f"
echo ""
echo "Other websites on this server were NOT modified."
