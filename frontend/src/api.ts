import type {
  DashboardFilter,
  DashboardQuery,
  DashboardResponse,
  FilterField,
  FilterRule,
  LivePriceRefreshResponse,
  LivePriceStatus,
  MarketDataStatus,
  PipelineTask,
  Stats,
  UploadResult,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = 25000;
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      ...init,
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timeoutId);
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function fetchStats() {
  return request<Stats>("/stats");
}

export function fetchMarketDataStatus() {
  return request<MarketDataStatus>("/market-data/status");
}

export function fyersLogin() {
  return request<{ started: boolean; token_ready?: boolean; message: string }>("/fyers/login", {
    method: "POST",
    body: "{}",
  });
}

export function fetchFyersStatus() {
  return request<MarketDataStatus["fyers"]>("/fyers/status");
}

export function fetchSectors() {
  return request<string[]>("/sectors");
}

export function fetchFilterFields() {
  return request<FilterField[]>("/filters/fields");
}

export function fetchDefaultFilterRules() {
  return request<FilterRule[]>("/filters/presets/default");
}

export function fetchDashboard(filters: DashboardFilter) {
  const params = new URLSearchParams();
  params.set("y_rank_max", String(filters.y_rank_max));
  params.set("q_rank_max", String(filters.q_rank_max));
  params.set("m_rank_max", String(filters.m_rank_max));
  params.set("rsi_avg_min", String(filters.rsi_avg_min));
  params.set("fno_only", String(filters.fno_only));
  if (filters.sector) params.set("sector", filters.sector);
  if (filters.search) params.set("search", filters.search);
  return request<DashboardResponse>(`/dashboard?${params.toString()}`);
}

export function queryDashboard(query: DashboardQuery) {
  return request<DashboardResponse>("/dashboard/query", {
    method: "POST",
    body: JSON.stringify(query),
  });
}

export function uploadRsiDigger(file: File) {
  const form = new FormData();
  form.append("file", file);
  return fetch(`${API_BASE}/upload/rsi-digger`, { method: "POST", body: form }).then(async (r) => {
    if (!r.ok) throw new Error(await r.text());
    return r.json() as Promise<UploadResult>;
  });
}

export function uploadFusionMatrix(file: File) {
  const form = new FormData();
  form.append("file", file);
  return fetch(`${API_BASE}/upload/fusion-matrix`, { method: "POST", body: form }).then(async (r) => {
    if (!r.ok) throw new Error(await r.text());
    return r.json() as Promise<UploadResult>;
  });
}

export function uploadFyersToken(file: File) {
  const form = new FormData();
  form.append("file", file);
  return fetch(`${API_BASE}/upload/fyers-token`, { method: "POST", body: form }).then(async (r) => {
    if (!r.ok) throw new Error(await r.text());
    return r.json() as Promise<UploadResult>;
  });
}

export function runPipeline(excelPath?: string) {
  return request<PipelineTask>("/pipeline/run", {
    method: "POST",
    body: JSON.stringify({
      import_excel: true,
      recalculate: true,
      excel_path: excelPath || null,
    }),
  });
}

export function runLiveRefresh(stockLimit?: number, syncFno = true) {
  return request<PipelineTask>("/pipeline/live-refresh", {
    method: "POST",
    body: JSON.stringify({ stock_limit: stockLimit ?? null, sync_fno: syncFno }),
  });
}

export function syncFnoList() {
  return request<Record<string, number>>("/pipeline/sync-fno", { method: "POST", body: "{}" });
}

export function recalculateRanks() {
  return request<PipelineTask>("/pipeline/recalculate-ranks", {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function fetchTasks() {
  return request<PipelineTask[]>("/pipeline/tasks?limit=10");
}

export function fetchLivePriceStatus() {
  return request<LivePriceStatus>("/live-prices/status");
}

export function startLivePrices() {
  return request<LivePriceStatus>("/live-prices/start", { method: "POST", body: "{}" });
}

export function stopLivePrices() {
  return request<LivePriceStatus>("/live-prices/stop", { method: "POST", body: "{}" });
}

export function setLivePriceWatch(scrips: string[]) {
  return request<{ watch_count: number; status: LivePriceStatus }>("/live-prices/watch", {
    method: "POST",
    body: JSON.stringify({ scrips }),
  });
}

export function refreshLivePrices(scrips: string[]) {
  return request<LivePriceRefreshResponse>("/live-prices/refresh", {
    method: "POST",
    body: JSON.stringify({ scrips }),
  });
}

export function livePricesWebSocketUrl() {
  const base = import.meta.env.VITE_API_BASE_URL || "/api";
  if (base.startsWith("http")) {
    return `${base.replace(/^http/, "ws")}/ws/live-prices`;
  }
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${base}/ws/live-prices`;
}
