# Deploy VS Dashboard on Hostinger VPS

Use this when you have **other websites on the same server**. The isolated installer only **adds** a new nginx vhost + systemd service — it does **not** remove `default` or change existing sites.

---

## Shared VPS (other sites already running) — use this

```bash
ssh lakhan@194.238.16.88

git clone https://github.com/LakhanT/vs-dashboard.git ~/vs-dashboard
cd ~/vs-dashboard
chmod +x deploy/hostinger/*.sh

# Use a SUBDOMAIN dedicated to this app (e.g. dashboard.yourdomain.com)
bash deploy/hostinger/install-isolated.sh dashboard.yourdomain.com 8010
```

Then SSL (only for your subdomain):

```bash
sudo certbot --nginx -d dashboard.yourdomain.com
```

Updates later:

```bash
bash ~/vs-dashboard/deploy/hostinger/deploy-isolated.sh
```

| What it touches | What it does NOT touch |
|-----------------|-------------------------|
| New file `/etc/nginx/sites-available/vs-dashboard` | Other `sites-enabled/*` entries |
| New systemd unit `vs-dashboard-api` | Apache/other app services |
| App files in `~/vs-dashboard` | `/var/www` or other project folders |
| localhost port `8010` (configurable) | Port 80/443 shared safely via `server_name` |

---

## Fresh VPS (no other sites)

See **install.sh** below for a full single-purpose server setup.

---

## Requirements

- Hostinger VPS (Ubuntu 22.04 or 24.04)
- Domain A record → VPS public IP
- SSH access as root or sudo user

---

## 1. DNS

In Hostinger (or your DNS panel):

| Type | Name | Value |
|------|------|--------|
| A | `@` or `dashboard` | Your VPS IP |
| A | `www` (optional) | Your VPS IP |

Wait a few minutes for DNS to propagate.

---

## 2. One-time install (on the VPS)

SSH into the server:

```bash
ssh root@YOUR_VPS_IP
```

Clone and run the installer (replace domain and email):

```bash
git clone https://github.com/LakhanT/vs-dashboard.git /opt/vs-dashboard
cd /opt/vs-dashboard
chmod +x deploy/hostinger/install.sh deploy/hostinger/deploy.sh
sudo bash deploy/hostinger/install.sh dashboard.yourdomain.com you@yourdomain.com
```

The script installs Python, Node, nginx, certbot, builds the frontend, starts the API service, and requests an SSL certificate.

---

## 3. Fyers live prices (optional)

Fyers OAuth uses a **local callback on port 5000** — run login once over SSH (not from the public website):

```bash
# On your PC: forward port 5000 to the VPS
ssh -L 5000:127.0.0.1:5000 root@YOUR_VPS_IP

# On the VPS (in another SSH session):
scp credentials.txt root@YOUR_VPS_IP:/opt/vs-dashboard/backend/
cd /opt/vs-dashboard/backend
sudo -u www-data /opt/vs-dashboard/backend/.venv/bin/python scripts/fyers_login.py
```

Or copy `backend/token.json` from your local machine after logging in at home:

```bash
scp backend/token.json root@YOUR_VPS_IP:/opt/vs-dashboard/backend/
sudo chown www-data:www-data /opt/vs-dashboard/backend/token.json
sudo systemctl restart vs-dashboard-api
```

Ensure Fyers app **Redirect URI** is `http://127.0.0.1:5000/callback` (same as local dev).

---

## 4. Updates (after you push to GitHub)

On the VPS:

```bash
sudo bash /opt/vs-dashboard/deploy/hostinger/deploy.sh
```

---

## Useful commands

| Task | Command |
|------|---------|
| API logs | `journalctl -u vs-dashboard-api -f` |
| Restart API | `systemctl restart vs-dashboard-api` |
| nginx test | `nginx -t && systemctl reload nginx` |
| Health check | `curl -s https://YOUR_DOMAIN/api/health` |

---

## Files

| File | Purpose |
|------|---------|
| `install.sh` | First-time VPS setup |
| `deploy.sh` | Pull latest code + rebuild |
| `nginx-vs-dashboard.conf.template` | nginx site config |
| `vs-dashboard-api.service` | systemd unit for FastAPI |
| `env.production.example` | Production environment template |

---

## Troubleshooting

**502 Bad Gateway** — API not running: `systemctl status vs-dashboard-api`

**Upload fails** — nginx body limit is 50MB in the site config.

**WebSocket / live prices** — ensure SSL is active (wss://). The nginx template already forwards `Upgrade` headers.

**SQLite data** — stored in `/opt/vs-dashboard/backend/data/vs_dashboard.db` (persists across deploys).
