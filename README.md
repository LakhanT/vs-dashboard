# VS Dashboard Web Application

A full-stack web dashboard that replicates the **VS Dashboard Open YQMWD 2026** Excel workflow with backend processing, database storage, background tasks, and a filterable web UI.

## Architecture

```
Excel / Yahoo / Definedge  →  Backend Pipeline  →  PostgreSQL/SQLite  →  API  →  React Dashboard
```

### Backend (`backend/`)
- **FastAPI** REST API
- **SQLAlchemy** models for stocks, OHLC, RSI, rankings, retracement, fusion, pipeline tasks
- **Background tasks** for Excel import pipeline
- **Celery** ready for Redis-based async jobs (optional)

### Frontend (`frontend/`)
- **React + TypeScript + Vite**
- Filter panel (Y/Q/M rank, RSI avg, sector, F&O, search)
- Dashboard output table
- Pipeline task monitor

## Live Data & Ranking Engine

The backend can now fetch **live NSE market data** and **compute rankings automatically** without relying on Excel for ranks.

### Data sources (priority order)
1. **Yahoo Finance** (`yfinance` 1.4+) — primary for **live price + OHLC** (batch download, `fast_info` for LTP)
2. **NSE archives** — official **F&O list** (211+ symbols from `fo_mktlots.csv`)
3. **NSE jugaad-data** — fallback OHLC when Yahoo misses a symbol

Set in `backend/.env`:
```
PRIMARY_DATA_SOURCE=yahoo
```

### Ranking formulas (matches Excel logic)

| Metric | Formula |
|--------|---------|
| **% Change from Open** | `(LTP - Period Open) / Period Open` |
| **Live Rank** | Rank by % change from open (descending) |
| **High Retracement** | `(LTP - Period High) / Period High` |
| **Green Range** | Same as % change from open |
| **RSI** | 14-period RSI on daily closes |

### File uploads (Definedge)

| Endpoint | File |
|----------|------|
| `POST /api/upload/rsi-digger` | RSI Digger Excel/CSV |
| `POST /api/upload/fusion-matrix` | Fusion Matrix Excel/CSV |

### Dynamic dashboard filters

| Endpoint | Description |
|----------|-------------|
| `GET /api/filters/fields` | All filterable columns |
| `GET /api/filters/presets/default` | Default Excel-style rules |
| `POST /api/dashboard/query` | Query with custom rules |

Example query body:
```json
{
  "logic": "and",
  "rules": [
    { "field": "y_rank", "operator": "lte", "value": 150 },
    { "field": "fusion_setup", "operator": "eq", "value": "Bullish" },
    { "field": "rsi_avg", "operator": "gt", "value": -2 }
  ],
  "search": "bank"
}
```

Filterable groups: **Stock**, **Price**, **Rankings**, **RSI**, **Retracement**, **Fusion**

### Pipeline endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/pipeline/live-refresh` | Yahoo live price + OHLC + ranks (+ optional F&O sync) |
| `POST /api/pipeline/sync-fno` | Sync F&O flags from NSE official list |
| `POST /api/pipeline/recalculate-ranks` | Recompute ranks from OHLC already in DB (no network) |
| `POST /api/pipeline/run` | Import from Excel (bootstrap / legacy) |

### Live refresh body
```json
{ "stock_limit": 50 }
```
Use `stock_limit` to control batch size (NSE fetches one symbol at a time; full universe ~750 stocks takes several minutes).

### Recommended workflow

1. **Import Excel once** — loads stock universe, sectors, F&O list
2. **Refresh Live Data** — updates prices, OHLC, rankings from NSE
3. **Apply filters** on dashboard — get filtered output

## Quick Start (Windows)

### One command — run everything

Double-click **`run.bat`** or run:

```powershell
cd "C:\Users\Lakhan\Desktop\VS Dashboard"
python run.py
```

This will:
1. Create backend venv + install dependencies (first run only)
2. Install frontend npm packages (first run only)
3. Create `.env` if missing
4. Start **backend** (port 8000) and **frontend** (port 5173)
5. Open the dashboard in your browser

Options:
```powershell
python run.py --setup-only   # install deps only
python run.py --no-browser   # don't auto-open browser
```

Open manually: **http://localhost:5173**

---

### Manual setup (optional)

#### 1. Backend setup

```powershell
cd "C:\Users\Lakhan\Desktop\VS Dashboard\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set:
```
EXCEL_IMPORT_PATH=C:\Users\Lakhan\Downloads\VS Dashboard Open YQMWD 2026.xlsx
```

Start API:
```powershell
uvicorn app.main:app --reload --port 8000
```

### 2. Frontend setup

```powershell
cd "C:\Users\Lakhan\Desktop\VS Dashboard\frontend"
npm install
npm run dev
```

Open: http://localhost:5173

### 3. Import data

Click **Run Excel Import Pipeline** in the UI, or call:

```powershell
curl -X POST http://127.0.0.1:8000/api/pipeline/run -H "Content-Type: application/json" -d "{\"import_excel\": true, \"recalculate\": true}"
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Health check |
| GET | `/api/stats` | System stats |
| GET | `/api/dashboard` | Filtered dashboard output |
| GET | `/api/sectors` | Sector list |
| GET | `/api/stocks` | Stock list |
| POST | `/api/pipeline/run` | Run import pipeline |
| GET | `/api/pipeline/tasks` | Task history |

## Dashboard Filters (from Excel Notes)

Default filters mirror the Excel Dashboard sheet:
- `Y.rank < 151`
- `Q.rank < 151`
- `M.rank < 151`
- `RSI avg > -2`
- Optional F&O filter

## Database

Default: **SQLite** (`vs_dashboard.db`) for local dev.

For production, set PostgreSQL in `.env`:
```
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/vs_dashboard
```

## Next Steps

1. **Yahoo/Tradepoint live fetch** — replace Excel-only import with scheduled OHLC updates
2. **Definedge RSI integration** — connect external RSI Digger source
3. **Ranking recalculation engine** — compute Y/Q/M ranks in backend instead of importing
4. **User auth** — add login and saved filter presets
5. **Redis + Celery** — for production-scale background jobs

## Project Structure

```
VS Dashboard/
├── backend/
│   ├── app/
│   │   ├── api/routes.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── services/pipeline.py
│   │   └── main.py
│   └── requirements.txt
└── frontend/
    └── src/
        ├── App.tsx
        ├── api.ts
        └── types.ts
```
