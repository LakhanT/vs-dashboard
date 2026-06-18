# Deploy VS Dashboard (GitHub + Vercel + Render)

This app has two parts:

| Part | Host | Why |
|------|------|-----|
| **Frontend** (React) | **Vercel** | Static Vite build |
| **Backend** (FastAPI, WebSocket, SQLite) | **Render** | Long-running server (Vercel cannot run this backend) |

> **Note:** Fyers live login on cloud needs extra setup (redirect URL, secrets). For a first live demo, the Render backend uses **Yahoo** for prices (`LIVE_PRICE_ENABLED=false` in `render.yaml`).

---

## 1. Push to GitHub

### A. Create a repo on GitHub

1. Open https://github.com/new
2. Name: `vs-dashboard` (or any name)
3. **Do not** add README / .gitignore (we already have them)
4. Click **Create repository**

### B. Push from your PC

In PowerShell (use your GitHub username and repo name):

```powershell
cd "C:\Users\Lakhan\Desktop\VS Dashboard"

$git = "C:\Program Files\Git\bin\git.exe"

& $git init
& $git add .
& $git commit -m "Initial commit: VS Dashboard web app"

& $git branch -M main
& $git remote add origin https://github.com/YOUR_USERNAME/vs-dashboard.git
& $git push -u origin main
```

When prompted, sign in with GitHub (browser or personal access token).

---

## 2. Deploy backend on Render

1. Go to https://render.com and sign up (GitHub login works).
2. **New → Blueprint** (or **New → Web Service**).
3. Connect your GitHub repo `vs-dashboard`.
4. If using **Blueprint**, Render reads `render.yaml` at the repo root.
5. Set environment variable **`CORS_ORIGINS`** to your Vercel URL after step 3, e.g.  
   `https://vs-dashboard.vercel.app`  
   (comma-separate multiple origins if needed.)
6. Deploy. Copy the API URL, e.g. `https://vs-dashboard-api.onrender.com`.

Optional Fyers on Render (advanced): set `LIVE_PRICE_SOURCE=fyers`, add `FYERS_CLIENT_ID`, `FYERS_SECRET_KEY`, and update Fyers app redirect URI to a public callback (not `127.0.0.1`).

---

## 3. Deploy frontend on Vercel

1. Go to https://vercel.com and sign up with GitHub.
2. **Add New → Project** → import `vs-dashboard`.
3. **Root Directory:** `frontend`
4. Framework: **Vite** (auto-detected)
5. **Environment variables:**

   | Name | Value |
   |------|--------|
   | `VITE_API_BASE_URL` | `https://YOUR-RENDER-APP.onrender.com/api` |

6. Deploy.

7. In **Render**, update `CORS_ORIGINS` to include your Vercel URL, then redeploy the API.

---

## 4. Verify

- Open your Vercel URL → dashboard loads
- Upload RSI Digger + Fusion on **Data** tab
- **Apply** filters

API health: `https://YOUR-RENDER-APP.onrender.com/api/health`

---

## Local development (unchanged)

```powershell
python run.py
```

Frontend: http://127.0.0.1:5173  
API: http://127.0.0.1:8000
