import { useCallback, useEffect, useRef, useState } from "react";
import type { DashboardResponse, FilterField, LivePriceTick, MarketDataStatus, SortLevel, Stats } from "./types";
import {
  fetchFilterFields,
  fetchMarketDataStatus,
  fetchStats,
  fetchTasks,
  fyersLogin,
  queryDashboard,
  recalculateRanks,
  runLiveRefresh,
  syncFnoList,
  uploadFusionMatrix,
  uploadFyersCredentials,
  uploadFyersToken,
  uploadRsiDigger,
} from "./api";
import { SheetFiltersPanel } from "./SheetFilters";
import {
  ALL_TABLE_COLUMNS,
  buildRulesFromSheetFilters,
  countActiveFilters,
  excelDefaultFilterState,
  isFnoOnlyFilter,
  ltpFilterMode,
  setFnoOnlyFilter,
  setLtpFilterMode,
  type SheetFilterState,
} from "./filterUtils";
import { useLivePrices } from "./useLivePrices";
import {
  dashboardExportFilename,
  downloadDashboardCsv,
  downloadDashboardXlsx,
} from "./exportDashboard";

const PAGE_SIZES = [25, 50, 100];
const MAX_SORT_LEVELS = 5;

const COLUMN_LABELS: Record<string, string> = {
  scrip: "Scrip",
  sector: "Sector",
  segment: "Segment",
  market_cap_cr: "Mkt Cap Cr",
  is_fno: "F&O",
  ltp: "LTP",
  pct_change_today: "% Today",
  y_rank: "Y Rank",
  q_rank: "Q Rank",
  m_rank: "M Rank",
  y_pct_change_open: "Y % Open",
  q_pct_change_open: "Q % Open",
  m_pct_change_open: "M % Open",
  y_high_retracement: "Y Hi Retr",
  rsi: "RSI",
  rsi_avg: "RSI Avg",
  rsi_diff: "RSI Diff",
  rsi_trend: "RSI Trend",
  crossover: "Crossover",
  retracement_from_high: "Retr High",
  green_range: "Green Rng",
  rise_from_low: "Rise Low",
  bullish_bo: "Bullish BO",
  fusion_setup: "Fusion",
  pf_perf_score: "PF Perf",
  pf_rank_score: "PF Rank",
  rs_perf_score: "RS Perf",
  rs_rank_score: "RS Rank",
  total_perf_score: "Tot Perf",
  total_ranking_score: "Tot Rank",
  net_perf_score: "Net Perf",
  net_ranking_score: "Net Rank",
  dtb_level: "DTB",
  dbs_level: "DBS",
  pct_from_dtb: "% DTB",
  pct_from_dbs: "% DBS",
};

function formatCell(key: string, value: unknown) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (
      key.includes("pct") ||
      key.includes("retracement") ||
      key.includes("green") ||
      key.includes("rise") ||
      key.includes("bullish") ||
      key === "pct_change_today"
    ) {
      const pct = Math.abs(value) <= 1 ? value * 100 : value;
      return `${pct.toFixed(2)}%`;
    }
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}

function isFnoStock(row: Record<string, unknown>): boolean {
  const v = row.is_fno;
  return v === true || v === "Yes" || v === 1 || v === "yes";
}

function columnLabel(col: string) {
  return COLUMN_LABELS[col] ?? col.replace(/_/g, " ");
}

function pctClass(key: string, value: unknown) {
  if (typeof value !== "number") return "";
  if (!key.includes("pct") && key !== "pct_change_today") return "";
  const n = Math.abs(value) <= 1 ? value : value / 100;
  if (n > 0) return "text-emerald-600";
  if (n < 0) return "text-rose-600";
  return "";
}

type SidebarTab = "filters" | "upload" | "stats";

function StatusDot({ ok, pulse }: { ok: boolean; pulse?: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${ok ? "bg-emerald-400" : "bg-amber-400"} ${pulse ? "animate-pulse" : ""}`}
    />
  );
}

export default function App() {
  const [filterFields, setFilterFields] = useState<FilterField[]>([]);
  const [sheetFilters, setSheetFilters] = useState<SheetFilterState>({});
  const [search, setSearch] = useState("");
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [pipelineMsg, setPipelineMsg] = useState<string | null>(null);
  const [liveLimit, setLiveLimit] = useState("");
  const [uploadMsg, setUploadMsg] = useState<string | null>(null);
  const [marketData, setMarketData] = useState<MarketDataStatus | null>(null);
  const [fyersLoginMsg, setFyersLoginMsg] = useState<string | null>(null);
  const [liveMode, setLiveMode] = useState(true);
  const [lastTickAt, setLastTickAt] = useState<string | null>(null);
  const [lastRefreshAt, setLastRefreshAt] = useState<string | null>(null);
  const [flashKeys, setFlashKeys] = useState<Record<string, number>>({});
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [downloadOpen, setDownloadOpen] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("upload");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sorts, setSorts] = useState<SortLevel[]>([{ field: "y_rank", dir: "asc" }]);
  const [highlightFno, setHighlightFno] = useState(() => {
    try {
      return localStorage.getItem("vs-highlight-fno") !== "false";
    } catch {
      return true;
    }
  });

  const skipSortReload = useRef(true);
  const filterFieldsRef = useRef(filterFields);
  const sheetFiltersRef = useRef(sheetFilters);
  const searchRef = useRef(search);
  filterFieldsRef.current = filterFields;
  sheetFiltersRef.current = sheetFilters;
  searchRef.current = search;

  const rows = data?.rows ?? [];
  const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageStart = (safePage - 1) * pageSize;
  const visibleRows = rows.slice(pageStart, pageStart + pageSize);
  const displayColumns = data?.columns?.length ? data.columns : ALL_TABLE_COLUMNS;
  const primarySort = sorts[0] ?? { field: "y_rank", dir: "asc" as const };

  const applyPriceTicks = useCallback((ticks: LivePriceTick[]) => {
    const changed: string[] = [];
    setData((prev) => {
      if (!prev) return prev;
      const byScrip = new Map(ticks.map((t) => [t.scrip.toUpperCase(), t]));
      const nextRows = prev.rows.map((row) => {
        const scripKey = String(row.scrip ?? "").toUpperCase();
        const tick = byScrip.get(scripKey);
        if (!tick) return row;
        const prevLtp = Number(row.ltp);
        const nextLtp = Number(tick.ltp);
        const moved =
          (!Number.isNaN(nextLtp) && Number.isNaN(prevLtp)) ||
          (!Number.isNaN(prevLtp) && !Number.isNaN(nextLtp) && Math.abs(prevLtp - nextLtp) > 0.0001);
        if (moved) changed.push(scripKey);
        return { ...row, ltp: tick.ltp, pct_change_today: tick.pct_change ?? row.pct_change_today };
      });
      return { ...prev, rows: nextRows };
    });
    if (changed.length > 0) {
      setFlashKeys((prev) => {
        const next = { ...prev };
        for (const scrip of changed) {
          next[scrip] = (next[scrip] ?? 0) + 1;
        }
        return next;
      });
    }
    setLastTickAt(new Date().toLocaleTimeString());
  }, []);

  const { status: liveStatus, connected: liveConnected, refreshing: liveRefreshing } = useLivePrices(
    liveMode,
    applyPriceTicks,
  );

  const loadDashboard = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (!opts?.silent) setLoading(true);
      setError(null);
      try {
        const dashboard = await queryDashboard({
          rules: buildRulesFromSheetFilters(filterFieldsRef.current, sheetFiltersRef.current),
          logic: "and",
          columns: ALL_TABLE_COLUMNS,
          search: searchRef.current || null,
          sorts,
          sort_by: sorts[0]?.field,
          sort_dir: sorts[0]?.dir,
          fresh: !opts?.silent,
        });
        setData(dashboard);
        setStats(await fetchStats());
        setLastRefreshAt(new Date().toLocaleTimeString());
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Failed to load dashboard";
        setError(msg.toLowerCase().includes("aborted") ? "Request timed out. Please try again." : msg);
      } finally {
        if (!opts?.silent) setLoading(false);
      }
    },
    [sorts],
  );

  const refreshMeta = useCallback(async () => {
    const [nextStats, md] = await Promise.all([fetchStats(), fetchMarketDataStatus()]);
    setStats(nextStats);
    setMarketData(md);
  }, []);

  useEffect(() => {
    void (async () => {
      const fields = await fetchFilterFields();
      setFilterFields(fields);
      const defaults = excelDefaultFilterState(fields);
      setSheetFilters(defaults);
      sheetFiltersRef.current = defaults;
      filterFieldsRef.current = fields;
      const [nextStats, md] = await Promise.all([fetchStats(), fetchMarketDataStatus()]);
      setStats(nextStats);
      setMarketData(md);
      await loadDashboard();
    })();
  }, []);

  useEffect(() => {
    if (marketData?.fyers.token_ready) return;
    const id = window.setInterval(() => void refreshMeta(), 2000);
    return () => window.clearInterval(id);
  }, [marketData?.fyers.token_ready, refreshMeta]);

  useEffect(() => {
    if (skipSortReload.current) {
      skipSortReload.current = false;
      return;
    }
    void loadDashboard();
  }, [sorts, loadDashboard]);

  useEffect(() => {
    setPage(1);
  }, [search, data?.matched_count, pageSize, sorts, sheetFilters]);

  useEffect(() => {
    if (!sidebarOpen && !toolsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setSidebarOpen(false);
        setToolsOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen, toolsOpen]);

  useEffect(() => {
    const id = window.setInterval(() => void refreshMeta(), 15000);
    return () => window.clearInterval(id);
  }, [refreshMeta]);

  useEffect(() => {
    if (!liveMode || !liveConnected) return;
    const id = window.setInterval(() => void loadDashboard({ silent: true }), 45000);
    return () => window.clearInterval(id);
  }, [liveMode, liveConnected, loadDashboard]);

  async function pollLatestTask() {
    setPipelineMsg("Computing OHLC, ranks & retracement…");
    const poll = window.setInterval(async () => {
      const nextTasks = await fetchTasks();
      const latest = nextTasks[0];
      if (latest?.status === "running") {
        setPipelineMsg(latest.result_summary || "Processing universe…");
      }
      if (latest && (latest.status === "success" || latest.status === "failed")) {
        window.clearInterval(poll);
        setPipelineRunning(false);
        setPipelineMsg(null);
        if (latest.status === "failed") setError(latest.error_message || "Task failed");
        else setUploadMsg(latest.result_summary || "Refresh complete");
        await loadDashboard();
        await refreshMeta();
      }
    }, 2000);
  }

  async function handleFyersLogin() {
    setFyersLoginMsg(null);
    setError(null);
    try {
      const res = await fyersLogin();
      if (res.token_ready) {
        setFyersLoginMsg("Fyers connected — live LTP enabled.");
        await refreshMeta();
        return;
      }
      setFyersLoginMsg(res.message);
      if (res.started) {
        const poll = window.setInterval(async () => {
          const md = await fetchMarketDataStatus();
          setMarketData(md);
          if (md.fyers.token_ready) {
            window.clearInterval(poll);
            setFyersLoginMsg("Fyers connected — live LTP enabled.");
          }
        }, 1500);
        window.setTimeout(() => window.clearInterval(poll), 200000);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Fyers login failed");
    }
  }

  async function handleFyersCredentialsUpload(file: File | null) {
    if (!file) return;
    setError(null);
    try {
      await uploadFyersCredentials(file);
      setFyersLoginMsg("Fyers credentials saved to server .env");
      await refreshMeta();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Credentials upload failed");
    }
  }

  async function handleFyersTokenUpload(file: File | null) {
    if (!file) return;
    setFyersLoginMsg(null);
    setError(null);
    try {
      const result = await uploadFyersToken(file);
      if (result.counts.token_ready) {
        setFyersLoginMsg("Fyers token uploaded — live LTP enabled.");
      } else {
        setFyersLoginMsg("Token saved but may be expired — log in on PC and upload again.");
      }
      await refreshMeta();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Token upload failed");
    }
  }

  async function handleUpload(kind: "rsi" | "fusion", file: File | null) {
    if (!file) return;
    setUploadMsg(null);
    setError(null);
    try {
      const result = kind === "rsi" ? await uploadRsiDigger(file) : await uploadFusionMatrix(file);
      setUploadMsg(`${kind === "rsi" ? "RSI" : "Fusion"}: ${result.counts.imported} rows imported`);
      if (result.refresh_task_id) {
        setPipelineRunning(true);
        void pollLatestTask();
      }
      await loadDashboard();
      await refreshMeta();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    }
  }

  const fyersReady = marketData?.fyers.token_ready ?? false;
  const universeCount = data?.total_stocks ?? stats?.rsi_universe_count ?? 0;
  const hasUniverse = universeCount > 0;
  const fnoOnly = isFnoOnlyFilter(sheetFilters);
  const ltpMode = ltpFilterMode(sheetFilters);

  const toggleFnoOnly = () => {
    const next = setFnoOnlyFilter(sheetFilters, !fnoOnly);
    setSheetFilters(next);
    sheetFiltersRef.current = next;
  };

  const setLtpMode = (mode: "all" | "has" | "missing") => {
    const next = setLtpFilterMode(sheetFilters, mode);
    setSheetFilters(next);
    sheetFiltersRef.current = next;
  };

  const toggleHighlightFno = () => {
    setHighlightFno((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("vs-highlight-fno", String(next));
      } catch {
        // ignore
      }
      return next;
    });
  };

  const handleDownload = (format: "csv" | "xlsx") => {
    if (!rows.length) return;
    setDownloadOpen(false);
    const filename = dashboardExportFilename(format, data?.as_of);
    if (format === "csv") {
      downloadDashboardCsv(displayColumns, rows, columnLabel, filename);
    } else {
      void downloadDashboardXlsx(displayColumns, rows, columnLabel, filename);
    }
  };

  const handleColumnSort = useCallback((col: string, multi: boolean) => {
    setSorts((prev) => {
      const idx = prev.findIndex((s) => s.field === col);
      if (multi) {
        if (idx >= 0) {
          const next = [...prev];
          next[idx] = { field: col, dir: prev[idx].dir === "asc" ? "desc" : "asc" };
          return next;
        }
        if (prev.length >= MAX_SORT_LEVELS) return prev;
        return [...prev, { field: col, dir: "asc" }];
      }
      if (idx === 0 && prev.length === 1) {
        return [{ field: col, dir: prev[0].dir === "asc" ? "desc" : "asc" }];
      }
      return [{ field: col, dir: "asc" }];
    });
  }, []);

  const clearExtraSorts = useCallback(() => {
    setSorts((prev) => (prev.length ? [prev[0]] : [{ field: "y_rank", dir: "asc" }]));
  }, []);

  const sortStackHint =
    sorts.length > 1
      ? sorts.map((s, i) => `${i + 1}. ${columnLabel(s.field)} ${s.dir === "asc" ? "↑" : "↓"}`).join(" → ")
      : null;

  const kpiCards = [
    { label: "Universe", value: universeCount || "—", hint: "RSI Digger stocks" },
    { label: "Matched", value: data?.matched_count ?? "—", hint: "After filters" },
    { label: "Fyers LTP", value: fyersReady ? "Ready" : "Login", hint: marketData?.live_price_source ?? "—" },
    { label: "Live feed", value: liveConnected ? "On" : "Off", hint: liveMode ? `${liveStatus?.universe_count ?? 0} universe` : "Paused" },
    { label: "As of", value: data?.as_of ?? "—", hint: lastRefreshAt ? `Refreshed ${lastRefreshAt}` : "—" },
  ];

  return (
    <div className="flex h-dvh min-h-0 flex-col overflow-hidden bg-slate-100 text-slate-800">
      {/* Header */}
      <header className="shrink-0 border-b border-slate-200 bg-white shadow-sm backdrop-blur-md">
        <div className="flex h-14 items-center gap-2 px-3 sm:gap-3 sm:px-4">
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            className="rounded-lg border border-slate-300 p-2 text-slate-600 hover:bg-slate-100 xl:hidden"
            aria-label="Open panel"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>

          <div className="flex min-w-0 items-center gap-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-sky-50 text-xs font-bold text-sky-600">
              VS
            </div>
            <div className="min-w-0 hidden sm:block">
              <h1 className="truncate text-sm font-semibold text-slate-900">YQMWD Dashboard</h1>
              <p className="text-[11px] text-slate-500">RSI + Fusion · Yahoo OHLC · Fyers LTP</p>
            </div>
          </div>

          <div className="mx-1 min-w-0 flex-1 sm:mx-2">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void loadDashboard()}
              placeholder="Search scrip or sector…"
              className="w-full rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 text-sm text-slate-900 placeholder:text-slate-500 focus:border-sky-400 focus:outline-none"
            />
          </div>

          <select
            value={ltpMode}
            onChange={(e) => setLtpMode(e.target.value as "all" | "has" | "missing")}
            title="Filter stocks by LTP availability"
            className="hidden shrink-0 rounded-lg border border-slate-300 bg-slate-50 px-2 py-2 text-xs text-slate-700 sm:block"
          >
            <option value="all">LTP: All</option>
            <option value="has">LTP: Has price</option>
            <option value="missing">LTP: Blank / 0</option>
          </select>

          <button
            type="button"
            onClick={toggleHighlightFno}
            title="Highlight F&O stocks in the table"
            className={`hidden shrink-0 rounded-full px-3 py-1.5 text-xs font-semibold sm:inline-flex ${
              highlightFno
                ? "bg-violet-100 text-violet-800 ring-1 ring-violet-400"
                : "bg-slate-100 text-slate-500 ring-1 ring-slate-300 hover:text-slate-700"
            }`}
          >
            Highlight F&O
          </button>

          <button
            type="button"
            onClick={toggleFnoOnly}
            title="Show only F&O (Futures & Options) stocks"
            className={`hidden shrink-0 rounded-full px-3 py-1.5 text-xs font-semibold sm:inline-flex ${
              fnoOnly
                ? "bg-violet-50 text-violet-700 ring-1 ring-violet-300"
                : "bg-slate-100 text-slate-500 ring-1 ring-slate-300 hover:text-slate-700"
            }`}
          >
            F&O only
          </button>

          <button
            type="button"
            onClick={() => setLiveMode((v) => !v)}
            className={`hidden shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold sm:flex ${
              liveMode ? "bg-emerald-50 text-emerald-600 ring-1 ring-emerald-300" : "bg-slate-100 text-slate-400 ring-1 ring-slate-300"
            }`}
          >
            <StatusDot ok={liveMode && liveConnected} pulse={liveRefreshing} />
            {liveMode ? "Live" : "Paused"}
          </button>

          <button
            type="button"
            onClick={() => void loadDashboard()}
            disabled={loading}
            className="shrink-0 rounded-lg bg-sky-600 px-3 py-2 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-60 sm:px-4"
          >
            {loading ? "…" : "Apply"}
          </button>

          <div className="relative shrink-0">
            <button
              type="button"
              onClick={() => setToolsOpen((v) => !v)}
              className="rounded-lg border border-slate-300 bg-slate-100 px-3 py-2 text-sm text-slate-700 hover:bg-slate-200"
            >
              Tools
            </button>
            {toolsOpen && (
              <>
                <button type="button" className="fixed inset-0 z-40" aria-label="Close" onClick={() => setToolsOpen(false)} />
                <div className="absolute right-0 z-50 mt-1 w-56 rounded-xl border border-slate-300 bg-white py-1 shadow-xl">
                  <label className="flex items-center justify-between px-4 py-2 text-sm text-slate-400">
                    OHLC limit
                    <input
                      type="number"
                      min={1}
                      max={2000}
                      placeholder="All"
                      value={liveLimit}
                      onChange={(e) => setLiveLimit(e.target.value)}
                      className="w-16 rounded border border-slate-300 bg-slate-50 px-2 py-1 text-sm"
                    />
                  </label>
                  <hr className="my-1 border-slate-200" />
                  {[
                    {
                      label: "Refresh OHLC & Ranks",
                      action: () => {
                        setToolsOpen(false);
                        setPipelineRunning(true);
                        void runLiveRefresh(Number(liveLimit) || undefined, true).then(() => pollLatestTask());
                      },
                    },
                    {
                      label: "Sync F&O List",
                      action: () => {
                        setToolsOpen(false);
                        void syncFnoList().then((r) => {
                          setUploadMsg(`F&O: ${r.fno_symbols} symbols`);
                          void loadDashboard();
                        });
                      },
                    },
                    {
                      label: "Recalculate Ranks",
                      action: () => {
                        setToolsOpen(false);
                        setPipelineRunning(true);
                        void recalculateRanks().then(() => pollLatestTask());
                      },
                    },
                  ].map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      disabled={pipelineRunning}
                      onClick={item.action}
                      className="block w-full px-4 py-2.5 text-left text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>

        {/* KPI strip */}
        <div className="grid grid-cols-2 gap-px border-t border-slate-200 bg-slate-100 sm:grid-cols-3 lg:grid-cols-5">
          {kpiCards.map((card) => (
            <div key={card.label} className="bg-white px-3 py-2">
              <p className="text-[10px] font-medium uppercase tracking-wider text-slate-500">{card.label}</p>
              <p className="truncate text-sm font-semibold text-slate-900">{card.value}</p>
              <p className="truncate text-[10px] text-slate-500">{card.hint}</p>
            </div>
          ))}
        </div>

        {/* Live / pipeline status */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-slate-200/80 bg-slate-50 px-3 py-1.5 text-[11px] text-slate-500">
          {pipelineRunning && (
            <span className="flex items-center gap-1.5 font-medium text-sky-600">
              <span className="h-1.5 w-8 animate-pulse-bar rounded bg-cyan-400" />
              {pipelineMsg ?? "Working…"}
            </span>
          )}
          {liveMode && (
            <>
              <span className={liveConnected ? "text-emerald-600" : "text-amber-600"}>
                {liveRefreshing
                  ? "Price tick"
                  : liveStatus?.stream_connected
                    ? "Fyers stream"
                    : liveConnected
                      ? "Poll mode"
                      : "Connecting…"}
              </span>
              {liveStatus?.universe_count != null && <span>{liveStatus.universe_count} universe</span>}
              {liveStatus?.last_quote_source && <span>via {liveStatus.last_quote_source}</span>}
              {lastTickAt && <span>Tick {lastTickAt}</span>}
            </>
          )}
          {uploadMsg && <span className="text-emerald-600/90">{uploadMsg}</span>}
        </div>
      </header>

      {/* Alerts */}
      {error && (
        <div className="shrink-0 border-b border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-700">
          {error}
          <button type="button" className="ml-3 underline" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}

      {!hasUniverse && !loading && !pipelineRunning && (
        <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          Start here: open <strong>Data</strong> panel → upload <strong>token.json</strong> → upload{" "}
          <strong>RSI Digger</strong> → upload <strong>Fusion Matrix</strong> → Apply filters.
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {sidebarOpen && (
          <button type="button" className="fixed inset-0 z-40 bg-slate-900/30 xl:hidden" aria-label="Close" onClick={() => setSidebarOpen(false)} />
        )}

        {/* Sidebar — always visible on xl */}
        <aside
          className={`flex w-full max-w-sm shrink-0 flex-col border-r border-slate-200 bg-white xl:static xl:flex xl:w-80 ${
            sidebarOpen ? "fixed inset-y-0 left-0 z-50 flex" : "hidden"
          }`}
        >
          <div className="flex shrink-0 border-b border-slate-200">
            {(
              [
                ["upload", "Data"],
                ["filters", "Filters"],
                ["stats", "Status"],
              ] as const
            ).map(([tab, label]) => (
              <button
                key={tab}
                type="button"
                onClick={() => setSidebarTab(tab)}
                className={`flex-1 py-3 text-xs font-semibold uppercase tracking-wide ${
                  sidebarTab === tab ? "border-b-2 border-sky-500 text-sky-600" : "text-slate-500 hover:text-slate-600"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto p-4">
            {sidebarTab === "upload" && (
              <div className="space-y-4">
                <section className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="mb-2 text-xs font-semibold text-slate-600">① Fyers live prices</p>
                  <div className="space-y-1 text-xs text-slate-400">
                    <p className="flex items-center gap-2">
                      <StatusDot ok={fyersReady} />
                      {fyersReady ? "Token active" : "Upload token.json"}
                    </p>
                    <p>OHLC: {marketData?.ohlc_source ?? "yahoo"}</p>
                  </div>
                  <label className="mt-3 flex cursor-pointer flex-col rounded-lg border border-dashed border-slate-300 p-3 hover:border-sky-400">
                    <span className="mb-1 text-xs text-slate-500">
                      Upload <strong>credentials.txt</strong> (App ID + Secret)
                    </span>
                    <input
                      type="file"
                      accept=".txt,text/plain"
                      onChange={(e) => void handleFyersCredentialsUpload(e.target.files?.[0] ?? null)}
                      className="text-xs text-sky-600"
                    />
                  </label>
                  <label className="mt-2 flex cursor-pointer flex-col rounded-lg border border-dashed border-slate-300 p-3 hover:border-sky-400">
                    <span className="mb-1 text-xs text-slate-500">
                      Upload <strong>token.json</strong> (after Fyers login on PC)
                    </span>
                    <input
                      type="file"
                      accept=".json,application/json"
                      onChange={(e) => void handleFyersTokenUpload(e.target.files?.[0] ?? null)}
                      className="text-xs text-sky-600"
                    />
                  </label>
                  {fyersReady && (
                    <p className="mt-2 text-xs text-emerald-600">✓ Connected — LTP updates live</p>
                  )}
                  {fyersLoginMsg && <p className="mt-2 text-xs text-emerald-600">{fyersLoginMsg}</p>}
                  <details className="mt-2 text-[10px] text-slate-400">
                    <summary className="cursor-pointer hover:text-slate-600">Login on this PC instead</summary>
                    <button
                      type="button"
                      onClick={() => void handleFyersLogin()}
                      disabled={marketData?.fyers.login_in_progress}
                      className="mt-2 w-full rounded-lg bg-slate-100 py-1.5 text-xs text-slate-600 hover:bg-slate-200 disabled:opacity-50"
                    >
                      {marketData?.fyers.login_in_progress ? "Logging in…" : "Browser login (local)"}
                    </button>
                  </details>
                </section>

                <section className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="mb-2 text-xs font-semibold text-slate-600">② RSI Digger (universe)</p>
                  <label className="flex cursor-pointer flex-col rounded-lg border border-dashed border-slate-300 p-3 hover:border-sky-400">
                    <span className="mb-1 text-xs text-slate-500">.xlsx / .csv</span>
                    <input
                      type="file"
                      accept=".xlsx,.xls,.csv"
                      onChange={(e) => void handleUpload("rsi", e.target.files?.[0] ?? null)}
                      className="text-xs text-sky-600"
                    />
                  </label>
                </section>

                <section className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                  <p className="mb-2 text-xs font-semibold text-slate-600">③ Fusion Matrix</p>
                  <label className="flex cursor-pointer flex-col rounded-lg border border-dashed border-slate-300 p-3 hover:border-sky-400">
                    <span className="mb-1 text-xs text-slate-500">.xlsx / .csv</span>
                    <input
                      type="file"
                      accept=".xlsx,.xls,.csv"
                      onChange={(e) => void handleUpload("fusion", e.target.files?.[0] ?? null)}
                      className="text-xs text-sky-600"
                    />
                  </label>
                </section>
              </div>
            )}

            {sidebarTab === "filters" && filterFields.length > 0 && (
              <SheetFiltersPanel
                fields={filterFields}
                value={sheetFilters}
                onChange={setSheetFilters}
                highlightFno={highlightFno}
                onHighlightFnoChange={toggleHighlightFno}
              />
            )}
            {sidebarTab === "filters" && filterFields.length === 0 && (
              <p className="text-xs text-slate-500">Loading filter fields…</p>
            )}

            {sidebarTab === "stats" && (
              <dl className="space-y-2 text-sm">
                {[
                  ["Universe (RSI)", data?.total_stocks ?? stats?.rsi_universe_count],
                  ["Filtered match", data?.matched_count],
                  ["F&O symbols", stats?.fno_stocks],
                  ["Active filters", countActiveFilters(sheetFilters)],
                  ["Ranking date", stats?.latest_ranking_date],
                  ["RSI snapshot", stats?.latest_rsi_date],
                  ["Pipeline", stats?.last_pipeline_task?.status],
                  ["Last task", stats?.last_pipeline_task?.result_summary],
                ].map(([label, val]) => (
                  <div key={String(label)} className="flex justify-between gap-2 rounded-lg bg-slate-50 px-3 py-2">
                    <dt className="text-slate-500">{label}</dt>
                    <dd className="truncate text-right font-medium text-slate-900">{val ?? "—"}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>

          <div className="shrink-0 border-t border-slate-200 p-3">
            <button
              type="button"
              onClick={() => {
                void loadDashboard();
                setSidebarOpen(false);
              }}
              disabled={loading}
              className="w-full rounded-xl bg-sky-600 py-2.5 text-sm font-semibold text-white hover:bg-sky-500 disabled:opacity-60"
            >
              {loading ? "Loading…" : "Apply Filters"}
            </button>
          </div>
        </aside>

        {/* Main table */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col">
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-3 py-2 sm:px-4">
            <p className="text-xs text-slate-400">
              <span className="font-medium text-slate-700">{rows.length}</span> rows · page {safePage}/{totalPages}
              {sortStackHint && (
                <span className="ml-2 hidden text-sky-700 sm:inline" title="Shift+click headers to add sort levels">
                  Sort: {sortStackHint}
                </span>
              )}
            </p>
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <div className="relative">
                <button
                  type="button"
                  disabled={rows.length === 0}
                  onClick={() => setDownloadOpen((v) => !v)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-300 bg-slate-50 px-2.5 py-1.5 font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
                  title="Download filtered dashboard"
                >
                  <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden>
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V4"
                    />
                  </svg>
                  Download
                </button>
                {downloadOpen && rows.length > 0 && (
                  <>
                    <button
                      type="button"
                      className="fixed inset-0 z-40"
                      aria-label="Close download menu"
                      onClick={() => setDownloadOpen(false)}
                    />
                    <div className="absolute right-0 z-50 mt-1 w-40 rounded-xl border border-slate-300 bg-white py-1 shadow-xl">
                      <button
                        type="button"
                        onClick={() => handleDownload("xlsx")}
                        className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-100"
                      >
                        Excel (.xlsx)
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDownload("csv")}
                        className="block w-full px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-100"
                      >
                        CSV (.csv)
                      </button>
                      <p className="border-t border-slate-200 px-4 py-2 text-[10px] text-slate-400">
                        {rows.length} filtered rows
                      </p>
                    </div>
                  </>
                )}
              </div>
              <span className="text-slate-500" title="Shift+click column headers to add secondary sorts">
                Sort
              </span>
              <select
                value={primarySort.field}
                onChange={(e) =>
                  setSorts((prev) => {
                    const dir = prev[0]?.dir ?? "asc";
                    const rest = prev.slice(1).filter((s) => s.field !== e.target.value);
                    return [{ field: e.target.value, dir }, ...rest];
                  })
                }
                className="rounded-lg border border-slate-300 bg-slate-50 px-2 py-1.5 text-slate-700"
              >
                {displayColumns.map((col) => (
                  <option key={col} value={col}>
                    {columnLabel(col)}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() =>
                  setSorts((prev) =>
                    prev.length
                      ? [{ ...prev[0], dir: prev[0].dir === "asc" ? "desc" : "asc" }, ...prev.slice(1)]
                      : [{ field: "y_rank", dir: "asc" }],
                  )
                }
                className="rounded-lg border border-slate-300 px-2 py-1.5 text-slate-600"
              >
                {primarySort.dir === "asc" ? "↑ Asc" : "↓ Desc"}
              </button>
              {sorts.length > 1 && (
                <button
                  type="button"
                  onClick={clearExtraSorts}
                  className="rounded-lg border border-slate-300 px-2 py-1.5 text-slate-500 hover:bg-slate-100"
                  title={sortStackHint ?? undefined}
                >
                  Clear {sorts.length - 1} extra
                </button>
              )}
            </div>
          </div>

          <div className="relative min-h-0 flex-1 overflow-auto">
            {loading && (
              <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-50 backdrop-blur-[1px]">
                <div className="rounded-xl border border-slate-300 bg-white px-6 py-4 text-sm text-slate-600">
                  Loading dashboard…
                </div>
              </div>
            )}

            <table className="table-sticky-first w-full min-w-[2400px] text-left text-sm">
              <thead className="sticky top-0 z-10 bg-white text-[11px] uppercase tracking-wide text-slate-500">
                <tr>
                  {displayColumns.map((col) => {
                    const sortIdx = sorts.findIndex((s) => s.field === col);
                    const inSort = sortIdx >= 0;
                    const level = sortIdx + 1;
                    const dir = sorts[sortIdx]?.dir;
                    return (
                      <th key={col} className="whitespace-nowrap px-3 py-2.5 font-semibold sm:px-4">
                        <button
                          type="button"
                          onClick={(e) => handleColumnSort(col, e.shiftKey)}
                          title={
                            inSort
                              ? `Sort level ${level} (${dir}). Shift+click to add/toggle in multi-sort.`
                              : `Sort by ${columnLabel(col)}. Shift+click to add as next sort level.`
                          }
                          className={`inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:bg-slate-100 hover:text-slate-800 ${
                            inSort ? "text-sky-700" : "text-slate-500"
                          }`}
                        >
                          <span>{columnLabel(col)}</span>
                          <span className="inline-flex min-w-[1.25rem] items-center justify-center text-[10px] tabular-nums" aria-hidden>
                            {inSort ? (
                              <>
                                {sorts.length > 1 && <span className="mr-0.5 font-bold">{level}</span>}
                                {dir === "asc" ? "▲" : "▼"}
                              </>
                            ) : (
                              "⇅"
                            )}
                          </span>
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, idx) => {
                  const scripKey = String(row.scrip ?? "").toUpperCase();
                  const flashKey = flashKeys[scripKey];
                  const fnoRow = highlightFno && isFnoStock(row);
                  return (
                  <tr
                    key={`${row.scrip}-${idx}`}
                    className={`border-t border-slate-200/50 hover:bg-slate-50 ${fnoRow ? "fno-row bg-violet-50/70" : ""}`}
                  >
                    {displayColumns.map((col) => (
                      <td
                        key={col}
                        className={`whitespace-nowrap px-3 py-2 sm:px-4 ${
                          col === "scrip"
                            ? `font-medium ${fnoRow ? "text-violet-900" : "text-slate-900"}`
                            : "text-slate-600"
                        } ${col === "is_fno" && fnoRow ? "font-semibold text-violet-700" : ""} ${
                          col === "ltp" && liveMode ? "font-semibold text-emerald-600" : ""
                        } ${pctClass(col, row[col])}`}
                      >
                        {col === "scrip" ? (
                          <span className="inline-flex items-center gap-1.5">
                            {formatCell(col, row[col])}
                            {fnoRow && (
                              <span className="rounded bg-violet-200/80 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-violet-800">
                                F&O
                              </span>
                            )}
                          </span>
                        ) : col === "ltp" ? (
                          <span key={flashKey ?? 0} className={flashKey ? "price-flash-cell" : undefined}>
                            {formatCell(col, row[col])}
                          </span>
                        ) : (
                          formatCell(col, row[col])
                        )}
                      </td>
                    ))}
                  </tr>
                  );
                })}
                {!loading && visibleRows.length === 0 && (
                  <tr>
                    <td colSpan={displayColumns.length} className="px-4 py-20 text-center text-slate-500">
                      No stocks matched. Upload RSI Digger in the Data panel, wait for ranks, then Apply.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-white px-3 py-2 sm:px-4">
            <p className="text-xs text-slate-400">
              {rows.length === 0
                ? "No results"
                : `${pageStart + 1}–${Math.min(pageStart + pageSize, rows.length)} of ${rows.length}`}
            </p>
            <div className="flex flex-wrap items-center gap-1.5">
              <select
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
                className="rounded border border-slate-300 bg-slate-50 px-2 py-1 text-xs"
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n}/page
                  </option>
                ))}
              </select>
              <button onClick={() => setPage(1)} disabled={safePage <= 1} className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-40">
                «
              </button>
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={safePage <= 1}
                className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-40"
              >
                ‹
              </button>
              <span className="min-w-[4rem] text-center text-xs text-slate-600">
                {safePage} / {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={safePage >= totalPages}
                className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-40"
              >
                ›
              </button>
              <button
                onClick={() => setPage(totalPages)}
                disabled={safePage >= totalPages}
                className="rounded border border-slate-300 px-2 py-1 text-xs disabled:opacity-40"
              >
                »
              </button>
            </div>
          </div>
        </main>
      </div>

      {/* Mobile bottom bar */}
      <div className="flex shrink-0 items-center gap-2 border-t border-slate-200 bg-white p-2 xl:hidden">
        <button
          type="button"
          onClick={() => {
            setSidebarTab("upload");
            setSidebarOpen(true);
          }}
          className="flex-1 rounded-lg border border-slate-300 py-2 text-xs font-medium text-slate-600"
        >
          Data
        </button>
        <button
          type="button"
          onClick={() => {
            setSidebarTab("filters");
            setSidebarOpen(true);
          }}
          className="flex-1 rounded-lg border border-slate-300 py-2 text-xs font-medium text-slate-600"
        >
          Filters
        </button>
        <button
          type="button"
          onClick={() => void loadDashboard()}
          disabled={loading}
          className="flex-1 rounded-lg bg-sky-600 py-2 text-xs font-semibold text-white disabled:opacity-60"
        >
          Apply
        </button>
        <button
          type="button"
          onClick={() => setLiveMode((v) => !v)}
          className={`rounded-lg px-3 py-2 text-xs font-medium ${liveMode ? "text-emerald-600" : "text-slate-500"}`}
        >
          {liveMode ? "Live" : "Off"}
        </button>
      </div>
        </div>
  );
}
