from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import PipelineTask, Stock
from app.schemas import (
    DashboardFilterIn,
    DashboardQueryIn,
    DashboardResponse,
    FilterFieldOut,
    LiveRefreshIn,
    PipelineTaskOut,
    RunPipelineIn,
    StatsOut,
    StockOut,
    UploadResult,
)
from app.services.dashboard_service import build_dashboard, query_dashboard
from app.services.dashboard_service import invalidate_universe_cache
from app.services.market_data import market_data_status
from app.services.file_import import import_fusion_matrix_file, import_rsi_digger_file
from app.services.filter_engine import default_preset_rules, get_filter_fields_payload
from app.services.pipeline import create_pipeline_task
from app.services.fno_sync import sync_fno_flags
from app.services.fyers import get_access_token, get_fyers_status, trigger_browser_login
from app.services.fyers_auth import import_fyers_token_bytes
from app.services.fyers_stream import fyers_stream_service
from app.services.live_price_service import live_price_service
from app.services.tasks import (
    get_stats,
    run_full_pipeline,
    run_live_refresh,
    run_recalculate_rankings,
)

router = APIRouter()
settings = get_settings()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}


@router.get("/market-data/status")
def get_market_data_status() -> dict:
    return market_data_status()


@router.get("/fyers/status")
def fyers_status() -> dict:
    return get_fyers_status()


@router.post("/fyers/login")
def fyers_login() -> dict:
    """Open browser Fyers login (OAuth callback on redirect URI port)."""
    result = trigger_browser_login()
    if not result.get("started") and not result.get("token_ready") and "not configured" in result.get("message", ""):
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.post("/fyers/refresh-token")
def refresh_fyers_token() -> dict:
    """Delete cached token and start browser login again."""
    get_access_token(force_refresh=True)
    result = trigger_browser_login()
    if not result.get("started"):
        raise HTTPException(status_code=400, detail=result.get("message", "Login could not start"))
    return {"status": "waiting", "message": result["message"]}


def _schedule_universe_refresh(
    background_tasks: BackgroundTasks,
    db: Session,
    *,
    trigger: str,
) -> int | None:
    """After RSI/Fusion upload, fetch Yahoo OHLC + Fyers LTP and compute ranks."""
    task = create_pipeline_task(
        db,
        task_type="live_refresh",
        payload={"trigger": trigger, "sync_fno": True},
    )
    background_tasks.add_task(run_live_refresh, task.id, None, True)
    return task.id


@router.get("/stats", response_model=StatsOut)
def stats() -> StatsOut:
    return get_stats()


@router.get("/filters/fields", response_model=list[FilterFieldOut])
def filter_fields() -> list[dict]:
    return get_filter_fields_payload()


@router.get("/filters/presets/default", response_model=list)
def default_filters():
    return [r.model_dump() for r in default_preset_rules()]


@router.get("/stocks", response_model=list[StockOut])
def list_stocks(
    db: Session = Depends(get_db),
    search: str | None = Query(default=None),
    fno_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[Stock]:
    stmt = select(Stock).order_by(Stock.scrip)
    if fno_only:
        stmt = stmt.where(Stock.is_fno.is_(True))
    if search:
        pattern = f"%{search.upper()}%"
        stmt = stmt.where(
            Stock.scrip.ilike(pattern)
            | Stock.ticker_symbol.ilike(pattern)
            | Stock.sector.ilike(pattern)
        )
    return list(db.scalars(stmt.limit(limit)))


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    db: Session = Depends(get_db),
    y_rank_max: int = Query(default=150, ge=1, le=1000),
    q_rank_max: int = Query(default=150, ge=1, le=1000),
    m_rank_max: int = Query(default=150, ge=1, le=1000),
    rsi_avg_min: float = Query(default=-2.0),
    fno_only: bool = Query(default=False),
    sector: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> DashboardResponse:
    filters = DashboardFilterIn(
        y_rank_max=y_rank_max,
        q_rank_max=q_rank_max,
        m_rank_max=m_rank_max,
        rsi_avg_min=rsi_avg_min,
        fno_only=fno_only,
        sector=sector,
        search=search,
    )
    return build_dashboard(db, filters)


@router.post("/dashboard/query", response_model=DashboardResponse)
def dashboard_query(payload: DashboardQueryIn, db: Session = Depends(get_db)) -> DashboardResponse:
    return query_dashboard(db, payload)


@router.get("/sectors")
def sectors(db: Session = Depends(get_db)) -> list[str]:
    rows = db.scalars(
        select(Stock.sector).where(Stock.sector.is_not(None)).distinct().order_by(Stock.sector)
    )
    return [row for row in rows if row]


@router.post("/upload/rsi-digger", response_model=UploadResult)
async def upload_rsi_digger(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")
    content = await file.read()
    try:
        counts = import_rsi_digger_file(db, content, file.filename)
        invalidate_universe_cache()
        refresh_task_id = _schedule_universe_refresh(background_tasks, db, trigger="rsi_upload")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadResult(
        import_type="rsi_digger",
        filename=file.filename,
        counts=counts,
        refresh_task_id=refresh_task_id,
    )


@router.post("/upload/fusion-matrix", response_model=UploadResult)
async def upload_fusion_matrix(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")
    content = await file.read()
    try:
        counts = import_fusion_matrix_file(db, content, file.filename)
        invalidate_universe_cache()
        refresh_task_id = _schedule_universe_refresh(background_tasks, db, trigger="fusion_upload")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadResult(
        import_type="fusion_matrix",
        filename=file.filename,
        counts=counts,
        refresh_task_id=refresh_task_id,
    )


@router.post("/upload/fyers-token", response_model=UploadResult)
async def upload_fyers_token(
    file: UploadFile = File(...),
) -> UploadResult:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")
    content = await file.read()
    try:
        meta = import_fyers_token_bytes(content, file.filename)
        if live_price_service.status.running:
            fyers_stream_service.stop()
            fyers_stream_service.start()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadResult(
        import_type="fyers_token",
        filename=file.filename,
        counts={"saved": 1, "token_ready": 1 if meta.get("token_ready") else 0},
        refresh_task_id=None,
    )


@router.post("/pipeline/run", response_model=PipelineTaskOut)
def run_pipeline(
    payload: RunPipelineIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> PipelineTask:
    excel_path = payload.excel_path or settings.excel_import_path
    if payload.import_excel and not excel_path:
        raise HTTPException(
            status_code=400,
            detail="excel_path is required. Set EXCEL_IMPORT_PATH in .env or pass excel_path in request.",
        )

    task = create_pipeline_task(
        db,
        task_type="full_pipeline",
        payload={"excel_path": excel_path, "recalculate": payload.recalculate},
    )

    if payload.import_excel:
        background_tasks.add_task(run_full_pipeline, task.id, excel_path)

    return task


@router.post("/pipeline/live-refresh", response_model=PipelineTaskOut)
def live_refresh(
    payload: LiveRefreshIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> PipelineTask:
    task = create_pipeline_task(
        db,
        task_type="live_refresh",
        payload={"stock_limit": payload.stock_limit, "sync_fno": payload.sync_fno},
    )
    background_tasks.add_task(run_live_refresh, task.id, payload.stock_limit, payload.sync_fno)
    return task


@router.post("/pipeline/sync-fno", response_model=dict)
def sync_fno(db: Session = Depends(get_db)) -> dict:
    try:
        return sync_fno_flags(db)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"F&O sync failed: {exc}") from exc


@router.post("/pipeline/recalculate-ranks", response_model=PipelineTaskOut)
def recalculate_ranks(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> PipelineTask:
    task = create_pipeline_task(db, task_type="recalculate_ranks", payload={})
    background_tasks.add_task(run_recalculate_rankings, task.id)
    return task


@router.get("/pipeline/tasks", response_model=list[PipelineTaskOut])
def list_tasks(db: Session = Depends(get_db), limit: int = Query(default=20, ge=1, le=100)) -> list[PipelineTask]:
    return list(
        db.scalars(select(PipelineTask).order_by(PipelineTask.created_at.desc()).limit(limit))
    )


@router.get("/pipeline/tasks/{task_id}", response_model=PipelineTaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)) -> PipelineTask:
    task = db.get(PipelineTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
