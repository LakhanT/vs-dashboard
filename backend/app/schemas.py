from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scrip: str
    ticker_symbol: str | None
    sector: str | None
    segment: str | None
    market_cap_cr: float | None
    is_fno: bool


class FilterRule(BaseModel):
    field: str
    operator: str
    value: str | float | int | bool | None = None


class DashboardFilterIn(BaseModel):
    y_rank_max: int = Field(default=150, ge=1, le=1000)
    q_rank_max: int = Field(default=150, ge=1, le=1000)
    m_rank_max: int = Field(default=150, ge=1, le=1000)
    rsi_avg_min: float = Field(default=-2.0)
    fno_only: bool = False
    sector: str | None = None
    search: str | None = None


class DashboardQueryIn(BaseModel):
    rules: list[FilterRule] = Field(default_factory=list)
    logic: str = Field(default="and", pattern="^(and|or)$")
    columns: list[str] | None = None
    search: str | None = None
    sort_by: str | None = None
    sort_dir: str = Field(default="asc", pattern="^(asc|desc)$")
    fresh: bool = True


class FilterFieldOut(BaseModel):
    key: str
    label: str
    type: str
    group: str
    operators: list[str]


class DashboardRowOut(BaseModel):
    scrip: str
    ticker_symbol: str | None = None
    sector: str | None = None
    segment: str | None = None
    market_cap_cr: float | None = None
    ltp: float | None = None
    pct_change_today: float | None = None
    y_rank: int | None = None
    q_rank: int | None = None
    m_rank: int | None = None
    y_pct_change_open: float | None = None
    q_pct_change_open: float | None = None
    m_pct_change_open: float | None = None
    y_high_retracement: float | None = None
    rsi: float | None = None
    rsi_avg: float | None = None
    rsi_diff: float | None = None
    rsi_trend: str | None = None
    crossover: str | None = None
    retracement_from_high: float | None = None
    green_range: float | None = None
    rise_from_low: float | None = None
    bullish_bo: float | None = None
    fusion_setup: str | None = None
    total_perf_score: float | None = None
    total_ranking_score: float | None = None
    net_perf_score: float | None = None
    pct_from_dtb: float | None = None
    pct_from_dbs: float | None = None
    is_fno: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class DashboardResponse(BaseModel):
    as_of: date | None
    total_stocks: int
    matched_count: int
    filters: DashboardFilterIn | None = None
    rules: list[FilterRule] = Field(default_factory=list)
    logic: str = "and"
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]]


class UploadResult(BaseModel):
    import_type: str
    filename: str
    counts: dict[str, int]
    refresh_task_id: int | None = None


class PipelineTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    status: TaskStatus
    result_summary: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class RunPipelineIn(BaseModel):
    import_excel: bool = True
    excel_path: str | None = None
    recalculate: bool = True


class LiveRefreshIn(BaseModel):
    stock_limit: int | None = Field(default=None, ge=1, le=2000)
    sync_fno: bool = True


class LivePriceScripsIn(BaseModel):
    scrips: list[str] = Field(default_factory=list, max_length=500)


class StatsOut(BaseModel):
    stocks: int
    rsi_universe_count: int
    fno_stocks: int
    latest_ranking_date: date | None
    latest_rsi_date: date | None
    last_pipeline_task: PipelineTaskOut | None
