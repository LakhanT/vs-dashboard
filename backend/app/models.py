from datetime import date, datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Timeframe(str, Enum):
    YEARLY = "yearly"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    DAILY = "daily"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class Stock(Base):
    __tablename__ = "stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scrip: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    ticker_symbol: Mapped[str | None] = mapped_column(String(32), index=True)
    sector: Mapped[str | None] = mapped_column(String(128))
    segment: Mapped[str | None] = mapped_column(String(64))
    market_cap_cr: Mapped[float | None] = mapped_column(Float)
    is_fno: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    prices = relationship("StockPrice", back_populates="stock", cascade="all, delete-orphan")
    ohlc_rows = relationship("OhlcBar", back_populates="stock", cascade="all, delete-orphan")
    rsi_rows = relationship("RsiSnapshot", back_populates="stock", cascade="all, delete-orphan")
    rankings = relationship("RankingSnapshot", back_populates="stock", cascade="all, delete-orphan")
    retracements = relationship("RetracementSnapshot", back_populates="stock", cascade="all, delete-orphan")
    fusion_rows = relationship("FusionSnapshot", back_populates="stock", cascade="all, delete-orphan")


class StockPrice(Base):
    __tablename__ = "stock_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    ltp: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    week_52_high: Mapped[float | None] = mapped_column(Float)
    week_52_low: Mapped[float | None] = mapped_column(Float)
    as_of: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    stock = relationship("Stock", back_populates="prices")


class OhlcBar(Base):
    __tablename__ = "ohlc_bars"
    __table_args__ = (UniqueConstraint("stock_id", "timeframe", "period_label", name="uq_ohlc_period"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    timeframe: Mapped[Timeframe] = mapped_column(SAEnum(Timeframe))
    period_label: Mapped[str] = mapped_column(String(32))
    period_start: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    lcp: Mapped[float | None] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)

    stock = relationship("Stock", back_populates="ohlc_rows")


class RsiSnapshot(Base):
    __tablename__ = "rsi_snapshots"
    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_rsi_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    rsi: Mapped[float | None] = mapped_column(Float)
    rsi_avg: Mapped[float | None] = mapped_column(Float)
    avg_diff: Mapped[float | None] = mapped_column(Float)
    rsi_change: Mapped[float | None] = mapped_column(Float)
    rsi_trend: Mapped[str | None] = mapped_column(String(64))
    rsi_diff: Mapped[float | None] = mapped_column(Float)
    crossover: Mapped[str | None] = mapped_column(String(64))
    ranking_rsi_positive: Mapped[int | None] = mapped_column(Integer)
    ranking_rsi_negative: Mapped[int | None] = mapped_column(Integer)

    stock = relationship("Stock", back_populates="rsi_rows")


class RankingSnapshot(Base):
    __tablename__ = "ranking_snapshots"
    __table_args__ = (
        UniqueConstraint("stock_id", "timeframe", "as_of", name="uq_rank_stock_tf_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    timeframe: Mapped[Timeframe] = mapped_column(SAEnum(Timeframe))
    as_of: Mapped[date] = mapped_column(Date, index=True)
    live_ranking: Mapped[int | None] = mapped_column(Integer, index=True)
    period_open: Mapped[float | None] = mapped_column(Float)
    pct_change_open: Mapped[float | None] = mapped_column(Float)
    pct_change_today: Mapped[float | None] = mapped_column(Float)
    high_retracement: Mapped[float | None] = mapped_column(Float)
    y_rank: Mapped[int | None] = mapped_column(Integer)
    m_rank: Mapped[int | None] = mapped_column(Integer)
    q_rank: Mapped[int | None] = mapped_column(Integer)

    stock = relationship("Stock", back_populates="rankings")


class RetracementSnapshot(Base):
    __tablename__ = "retracement_snapshots"
    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_retracement_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    pre_high: Mapped[float | None] = mapped_column(Float)
    ltp: Mapped[float | None] = mapped_column(Float)
    pct_today: Mapped[float | None] = mapped_column(Float)
    green_range: Mapped[float | None] = mapped_column(Float)
    retracement_from_high: Mapped[float | None] = mapped_column(Float)
    rise_from_low: Mapped[float | None] = mapped_column(Float)
    bullish_bo: Mapped[float | None] = mapped_column(Float)
    yearly_ranking: Mapped[int | None] = mapped_column(Integer)
    rsi_diff: Mapped[float | None] = mapped_column(Float)
    crossover: Mapped[str | None] = mapped_column(String(64))

    stock = relationship("Stock", back_populates="retracements")


class FusionSnapshot(Base):
    __tablename__ = "fusion_snapshots"
    __table_args__ = (UniqueConstraint("stock_id", "as_of", name="uq_fusion_stock_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    setup: Mapped[str | None] = mapped_column(String(32))
    pf_perf_score: Mapped[float | None] = mapped_column(Float)
    pf_perf_t0025: Mapped[float | None] = mapped_column(Float)
    pf_perf_t01: Mapped[float | None] = mapped_column(Float)
    pf_perf_t02: Mapped[float | None] = mapped_column(Float)
    pf_perf_t03: Mapped[float | None] = mapped_column(Float)
    pf_rank_score: Mapped[float | None] = mapped_column(Float)
    pf_rank_t0025: Mapped[float | None] = mapped_column(Float)
    pf_rank_t01: Mapped[float | None] = mapped_column(Float)
    pf_rank_t02: Mapped[float | None] = mapped_column(Float)
    pf_rank_t03: Mapped[float | None] = mapped_column(Float)
    rs_perf_score: Mapped[float | None] = mapped_column(Float)
    rs_perf_t0025: Mapped[float | None] = mapped_column(Float)
    rs_perf_t01: Mapped[float | None] = mapped_column(Float)
    rs_perf_t02: Mapped[float | None] = mapped_column(Float)
    rs_perf_t03: Mapped[float | None] = mapped_column(Float)
    rs_rank_score: Mapped[float | None] = mapped_column(Float)
    rs_rank_t0025: Mapped[float | None] = mapped_column(Float)
    rs_rank_t01: Mapped[float | None] = mapped_column(Float)
    rs_rank_t02: Mapped[float | None] = mapped_column(Float)
    rs_rank_t03: Mapped[float | None] = mapped_column(Float)
    total_perf_score: Mapped[float | None] = mapped_column(Float)
    total_ranking_score: Mapped[float | None] = mapped_column(Float)
    net_perf_score: Mapped[float | None] = mapped_column(Float)
    net_ranking_score: Mapped[float | None] = mapped_column(Float)
    dtb_level: Mapped[float | None] = mapped_column(Float)
    dbs_level: Mapped[float | None] = mapped_column(Float)
    pct_from_dtb: Mapped[float | None] = mapped_column(Float)
    pct_from_dbs: Mapped[float | None] = mapped_column(Float)

    stock = relationship("Stock", back_populates="fusion_rows")


class DashboardFilterPreset(Base):
    __tablename__ = "dashboard_filter_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    y_rank_max: Mapped[int] = mapped_column(Integer, default=150)
    q_rank_max: Mapped[int] = mapped_column(Integer, default=150)
    m_rank_max: Mapped[int] = mapped_column(Integer, default=150)
    rsi_avg_min: Mapped[float] = mapped_column(Float, default=-2.0)
    fno_only: Mapped[bool] = mapped_column(Boolean, default=False)
    sector: Mapped[str | None] = mapped_column(String(128))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class PipelineTask(Base):
    __tablename__ = "pipeline_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[TaskStatus] = mapped_column(SAEnum(TaskStatus), default=TaskStatus.PENDING)
    payload: Mapped[str | None] = mapped_column(Text)
    result_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
