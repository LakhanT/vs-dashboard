from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.ws_routes import router as ws_router
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.db_migrate import ensure_sqlite_columns
from app.services.pipeline import _ensure_default_preset
from app.services.live_price_service import live_price_service

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_columns(engine)
    db = SessionLocal()
    try:
        _ensure_default_preset(db)
        db.commit()
    finally:
        db.close()
    if settings.live_price_enabled:
        live_price_service.start()
    yield
    live_price_service.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
app.include_router(ws_router, prefix="/api")
