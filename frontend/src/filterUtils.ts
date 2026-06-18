import type { FilterField, FilterRule } from "./types";

/** Excel sheet names → API filter groups */
export const SHEET_GROUPS: { key: string; label: string }[] = [
  { key: "Stock", label: "Stock / Universe" },
  { key: "Price", label: "Microsoft Price" },
  { key: "Rankings", label: "Y · Q · M Rank" },
  { key: "RSI", label: "RSI Digger" },
  { key: "Retracement", label: "Retracement" },
  { key: "Fusion", label: "Fusion Matrix" },
];

export type FieldFilterInput = {
  min: string;
  max: string;
  text: string;
  bool: "" | "yes" | "no";
};

export type SheetFilterState = Record<string, FieldFilterInput>;

/** PF/RS score groups — tier columns left, main score label right (matches Excel Fusion Matrix). */
export const FUSION_SCORE_GROUPS: {
  label: string;
  mainKey: string;
  tiers: { key: string; label: string }[];
}[] = [
  {
    label: "PF Performance Score",
    mainKey: "pf_perf_score",
    tiers: [
      { key: "pf_perf_t0025", label: "0.25" },
      { key: "pf_perf_t01", label: "1" },
      { key: "pf_perf_t02", label: "2" },
      { key: "pf_perf_t03", label: "3" },
    ],
  },
  {
    label: "PF Ranking Score",
    mainKey: "pf_rank_score",
    tiers: [
      { key: "pf_rank_t0025", label: "0.25" },
      { key: "pf_rank_t01", label: "1" },
      { key: "pf_rank_t02", label: "2" },
      { key: "pf_rank_t03", label: "3" },
    ],
  },
  {
    label: "RS Performance Score",
    mainKey: "rs_perf_score",
    tiers: [
      { key: "rs_perf_t0025", label: "0.25" },
      { key: "rs_perf_t01", label: "1" },
      { key: "rs_perf_t02", label: "2" },
      { key: "rs_perf_t03", label: "3" },
    ],
  },
  {
    label: "RS Ranking Score",
    mainKey: "rs_rank_score",
    tiers: [
      { key: "rs_rank_t0025", label: "0.25" },
      { key: "rs_rank_t01", label: "1" },
      { key: "rs_rank_t02", label: "2" },
      { key: "rs_rank_t03", label: "3" },
    ],
  },
];

export const FUSION_GROUPED_FIELD_KEYS = new Set(
  FUSION_SCORE_GROUPS.flatMap((g) => [g.mainKey, ...g.tiers.map((t) => t.key)]),
);

export function emptyFieldInput(): FieldFilterInput {
  return { min: "", max: "", text: "", bool: "" };
}

export function emptyFilterState(fields: FilterField[]): SheetFilterState {
  return Object.fromEntries(fields.map((f) => [f.key, emptyFieldInput()]));
}

export function excelDefaultFilterState(fields: FilterField[]): SheetFilterState {
  const state = emptyFilterState(fields);
  const set = (key: string, patch: Partial<FieldFilterInput>) => {
    if (state[key]) Object.assign(state[key], patch);
  };
  set("y_rank", { max: "150" });
  set("q_rank", { max: "150" });
  set("m_rank", { max: "150" });
  set("rsi_avg", { min: "-2" });
  return state;
}

export function isFnoOnlyFilter(inputs: SheetFilterState): boolean {
  return inputs.is_fno?.bool === "yes";
}

export function setFnoOnlyFilter(inputs: SheetFilterState, enabled: boolean): SheetFilterState {
  return {
    ...inputs,
    is_fno: { ...emptyFieldInput(), bool: enabled ? "yes" : "" },
  };
}

export function isHideNoLtpFilter(inputs: SheetFilterState): boolean {
  return inputs.has_ltp?.bool === "yes";
}

export function isNoLtpOnlyFilter(inputs: SheetFilterState): boolean {
  return inputs.has_ltp?.bool === "no";
}

export function setHideNoLtpFilter(inputs: SheetFilterState, enabled: boolean): SheetFilterState {
  return {
    ...inputs,
    has_ltp: { ...emptyFieldInput(), bool: enabled ? "yes" : "" },
  };
}

export function setNoLtpOnlyFilter(inputs: SheetFilterState, enabled: boolean): SheetFilterState {
  return {
    ...inputs,
    has_ltp: { ...emptyFieldInput(), bool: enabled ? "no" : "" },
  };
}

export function ltpFilterMode(inputs: SheetFilterState): "all" | "has" | "missing" {
  const v = inputs.has_ltp?.bool;
  if (v === "yes") return "has";
  if (v === "no") return "missing";
  return "all";
}

export function setLtpFilterMode(inputs: SheetFilterState, mode: "all" | "has" | "missing"): SheetFilterState {
  const bool = mode === "has" ? "yes" : mode === "missing" ? "no" : "";
  return {
    ...inputs,
    has_ltp: { ...emptyFieldInput(), bool },
  };
}

function coercePercentValue(field: FilterField, raw: string): number | null {
  const n = Number(raw);
  if (Number.isNaN(n)) return null;
  if (field.type === "percent" && Math.abs(n) > 1) return n / 100;
  return n;
}

export function buildRulesFromSheetFilters(
  fields: FilterField[],
  inputs: SheetFilterState,
): FilterRule[] {
  const rules: FilterRule[] = [];

  for (const field of fields) {
    const inp = inputs[field.key];
    if (!inp) continue;

    if (field.type === "boolean") {
      if (inp.bool === "yes") rules.push({ field: field.key, operator: "is_true", value: true });
      if (inp.bool === "no") rules.push({ field: field.key, operator: "is_false", value: true });
      continue;
    }

    if (field.type === "string") {
      if (inp.text.trim()) {
        rules.push({ field: field.key, operator: "contains", value: inp.text.trim() });
      }
      continue;
    }

    if (inp.min.trim() !== "") {
      const v = coercePercentValue(field, inp.min.trim());
      if (v !== null) rules.push({ field: field.key, operator: "gte", value: v });
    }
    if (inp.max.trim() !== "") {
      const v = coercePercentValue(field, inp.max.trim());
      if (v !== null) rules.push({ field: field.key, operator: "lte", value: v });
    }
  }

  return rules;
}

export function countActiveFilters(inputs: SheetFilterState): number {
  return Object.values(inputs).filter(
    (inp) => inp.min !== "" || inp.max !== "" || inp.text.trim() !== "" || inp.bool !== "",
  ).length;
}

export const ALL_TABLE_COLUMNS = [
  "scrip",
  "sector",
  "segment",
  "market_cap_cr",
  "is_fno",
  "ltp",
  "pct_change_today",
  "y_rank",
  "q_rank",
  "m_rank",
  "y_pct_change_open",
  "q_pct_change_open",
  "m_pct_change_open",
  "y_high_retracement",
  "rsi",
  "rsi_avg",
  "rsi_diff",
  "rsi_trend",
  "crossover",
  "retracement_from_high",
  "green_range",
  "rise_from_low",
  "bullish_bo",
  "fusion_setup",
  "pf_perf_score",
  "pf_rank_score",
  "rs_perf_score",
  "rs_rank_score",
  "total_perf_score",
  "total_ranking_score",
  "net_perf_score",
  "net_ranking_score",
  "dtb_level",
  "dbs_level",
  "pct_from_dtb",
  "pct_from_dbs",
];
