from datetime import datetime

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import PipelineTask, RankingSnapshot, RsiSnapshot, Stock, TaskStatus
from app.schemas import PipelineTaskOut, StatsOut
from app.services.pipeline import import_excel_workbook, mark_task_failed, mark_task_running, mark_task_success
from app.services.ranking_engine import recalculate_rankings_from_db, refresh_live_data
from app.services.dashboard_service import invalidate_universe_cache
from app.services.universe import count_rsi_universe, latest_rsi_date


def run_full_pipeline(task_id: int, excel_path: str) -> None:
    db = SessionLocal()
    task = db.get(PipelineTask, task_id)
    if task is None:
        db.close()
        return

    try:
        mark_task_running(db, task)
        counts = import_excel_workbook(db, excel_path)
        summary = ", ".join(f"{key}={value}" for key, value in counts.items())
        mark_task_success(db, task, summary)
        invalidate_universe_cache()
    except Exception as exc:  # noqa: BLE001
        mark_task_failed(db, task, str(exc))
        invalidate_universe_cache()
    finally:
        db.close()


def run_live_refresh(task_id: int, stock_limit: int | None = None, sync_fno: bool = True) -> None:
    db = SessionLocal()
    task = db.get(PipelineTask, task_id)
    if task is None:
        db.close()
        return

    try:
        mark_task_running(db, task)
        counts = refresh_live_data(db, stock_limit=stock_limit, sync_fno=sync_fno)
        summary = ", ".join(f"{key}={value}" for key, value in counts.items())
        mark_task_success(db, task, summary)
        invalidate_universe_cache()
    except Exception as exc:  # noqa: BLE001
        mark_task_failed(db, task, str(exc))
        invalidate_universe_cache()
    finally:
        db.close()


def run_recalculate_rankings(task_id: int) -> None:
    db = SessionLocal()
    task = db.get(PipelineTask, task_id)
    if task is None:
        db.close()
        return

    try:
        mark_task_running(db, task)
        counts = recalculate_rankings_from_db(db)
        summary = ", ".join(f"{key}={value}" for key, value in counts.items())
        mark_task_success(db, task, summary)
        invalidate_universe_cache()
    except Exception as exc:  # noqa: BLE001
        mark_task_failed(db, task, str(exc))
        invalidate_universe_cache()
    finally:
        db.close()


def get_stats() -> StatsOut:
    db = SessionLocal()
    try:
        stocks = db.scalar(select(func.count()).select_from(Stock)) or 0
        rsi_universe_count = count_rsi_universe(db)
        fno_stocks = db.scalar(select(func.count()).select_from(Stock).where(Stock.is_fno.is_(True))) or 0
        latest_ranking_date = db.scalar(select(func.max(RankingSnapshot.as_of)))
        latest_rsi = latest_rsi_date(db)
        last_task = db.scalar(select(PipelineTask).order_by(PipelineTask.created_at.desc()).limit(1))
        last_pipeline_task = PipelineTaskOut.model_validate(last_task) if last_task else None
        return StatsOut(
            stocks=stocks,
            rsi_universe_count=rsi_universe_count,
            fno_stocks=fno_stocks,
            latest_ranking_date=latest_ranking_date,
            latest_rsi_date=latest_rsi,
            last_pipeline_task=last_pipeline_task,
        )
    finally:
        db.close()
