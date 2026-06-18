from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "vs_dashboard",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
)


@celery_app.task(name="vs_dashboard.run_pipeline")
def run_pipeline_task(task_id: int, excel_path: str) -> None:
    from app.services.tasks import run_full_pipeline

    run_full_pipeline(task_id, excel_path)
