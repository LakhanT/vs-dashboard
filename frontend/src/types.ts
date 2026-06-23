export type FilterRule = {
  field: string;
  operator: string;
  value: string | number | boolean | null;
};

export type FilterField = {
  key: string;
  label: string;
  type: string;
  group: string;
  operators: string[];
};

export type SortLevel = {
  field: string;
  dir: "asc" | "desc";
};

export type DashboardQuery = {
  rules: FilterRule[];
  logic: "and" | "or";
  columns?: string[] | null;
  search?: string | null;
  sort_by?: string | null;
  sort_dir?: "asc" | "desc";
  sorts?: SortLevel[];
  fresh?: boolean;
};

export type DashboardFilter = {
  y_rank_max: number;
  q_rank_max: number;
  m_rank_max: number;
  rsi_avg_min: number;
  fno_only: boolean;
  sector?: string | null;
  search?: string | null;
};

export type DashboardResponse = {
  as_of: string | null;
  total_stocks: number;
  matched_count: number;
  filters?: DashboardFilter | null;
  rules: FilterRule[];
  logic: string;
  columns: string[];
  rows: Record<string, string | number | boolean | null>[];
};

export type Stats = {
  stocks: number;
  rsi_universe_count: number;
  fno_stocks: number;
  latest_ranking_date: string | null;
  latest_rsi_date: string | null;
  last_pipeline_task: PipelineTask | null;
};

export type PipelineTask = {
  id: number;
  task_type: string;
  status: "pending" | "running" | "success" | "failed";
  result_summary: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
};

export type UploadResult = {
  import_type: string;
  filename: string;
  counts: Record<string, number>;
  refresh_task_id?: number | null;
};

export type MarketDataStatus = {
  ohlc_source: string;
  live_price_source: string;
  fyers: {
    configured: boolean;
    client_id_set: boolean;
    token_ready: boolean;
    login_in_progress?: boolean;
    redirect_uri?: string;
  };
  inputs: string[];
  computed: string[];
};

export type LivePriceStatus = {
  running: boolean;
  interval_sec: number;
  batch_size: number;
  last_run_at: string | null;
  last_duration_sec: number | null;
  last_updated: number;
  last_error: string | null;
  last_quote_source: string | null;
  mode?: string;
  watch_count?: number;
  stream_connected?: boolean;
  total_ticks?: number;
  total_cycles: number;
  cursor: number;
  universe_count: number;
  subscribers: number;
};

export type LivePriceTick = {
  scrip: string;
  ltp: number;
  pct_change: number | null;
  source?: string;
  at?: string;
};

export type LiveRankSnapshotRow = {
  scrip: string;
  y_rank: number | null;
  q_rank: number | null;
  m_rank: number | null;
  y_open: number | null;
  y_high: number | null;
  y_low: number | null;
  y_close: number | null;
  q_open: number | null;
  q_high: number | null;
  q_low: number | null;
  q_close: number | null;
  m_open: number | null;
  m_high: number | null;
  m_low: number | null;
  m_close: number | null;
  y_pct_change_open: number | null;
  q_pct_change_open: number | null;
  m_pct_change_open: number | null;
  y_high_retracement: number | null;
  green_range: number | null;
  retracement_from_high: number | null;
  rise_from_low: number | null;
  bullish_bo: number | null;
};

export type LivePriceRefreshResponse = {
  type: string;
  updated: number;
  at: string;
  prices: LivePriceTick[];
};
