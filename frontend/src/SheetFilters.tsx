import { useMemo, useState } from "react";
import type { FilterField } from "./types";
import {
  SHEET_GROUPS,
  FUSION_GROUPED_FIELD_KEYS,
  FUSION_SCORE_GROUPS,
  type FieldFilterInput,
  type SheetFilterState,
  countActiveFilters,
  emptyFieldInput,
  emptyFilterState,
  excelDefaultFilterState,
  isFnoOnlyFilter,
  ltpFilterMode,
  setFnoOnlyFilter,
  setLtpFilterMode,
} from "./filterUtils";

type Props = {
  fields: FilterField[];
  value: SheetFilterState;
  onChange: (next: SheetFilterState) => void;
  highlightFno?: boolean;
  onHighlightFnoChange?: () => void;
};

function FieldRow({
  field,
  input,
  onPatch,
}: {
  field: FilterField;
  input: FieldFilterInput;
  onPatch: (patch: Partial<FieldFilterInput>) => void;
}) {
  if (field.type === "boolean") {
    return (
      <label className="flex items-center justify-between gap-2 py-1.5">
        <span className="text-xs text-slate-600">{field.label}</span>
        <select
          value={input.bool}
          onChange={(e) => onPatch({ bool: e.target.value as FieldFilterInput["bool"] })}
          className="w-24 rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900"
        >
          <option value="">Any</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      </label>
    );
  }

  if (field.type === "string") {
    return (
      <label className="block py-1.5">
        <span className="mb-1 block text-xs text-slate-600">{field.label}</span>
        <input
          type="text"
          value={input.text}
          onChange={(e) => onPatch({ text: e.target.value })}
          placeholder="Contains…"
          className="w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900"
        />
      </label>
    );
  }

  const hint = field.type === "percent" ? " (use % or decimal)" : "";
  return (
    <div className="py-1.5">
      <span className="mb-1 block text-xs text-slate-600">
        {field.label}
        <span className="text-slate-600">{hint}</span>
      </span>
      <div className="flex gap-2">
        <input
          type="number"
          value={input.min}
          onChange={(e) => onPatch({ min: e.target.value })}
          placeholder="Min ≥"
          className="w-1/2 rounded border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900"
        />
        <input
          type="number"
          value={input.max}
          onChange={(e) => onPatch({ max: e.target.value })}
          placeholder="Max ≤"
          className="w-1/2 rounded border border-slate-300 bg-white px-2 py-1.5 text-xs text-slate-900"
        />
      </div>
    </div>
  );
}

function TierMiniFilter({
  label,
  input,
  onPatch,
}: {
  label: string;
  input: FieldFilterInput;
  onPatch: (patch: Partial<FieldFilterInput>) => void;
}) {
  return (
    <div className="min-w-0">
      <span className="mb-0.5 block truncate text-center text-[10px] font-semibold text-amber-700">
        {label}
      </span>
      <div className="flex gap-0.5">
        <input
          type="number"
          value={input.min}
          onChange={(e) => onPatch({ min: e.target.value })}
          placeholder="≥"
          className="min-w-0 flex-1 rounded border border-slate-300 bg-white px-0.5 py-0.5 text-center text-[10px] text-slate-900"
        />
        <input
          type="number"
          value={input.max}
          onChange={(e) => onPatch({ max: e.target.value })}
          placeholder="≤"
          className="min-w-0 flex-1 rounded border border-slate-300 bg-white px-0.5 py-0.5 text-center text-[10px] text-slate-900"
        />
      </div>
    </div>
  );
}

function FusionScoreGroupRow({
  group,
  value,
  onPatchField,
}: {
  group: (typeof FUSION_SCORE_GROUPS)[number];
  value: SheetFilterState;
  onPatchField: (key: string, patch: Partial<FieldFilterInput>) => void;
}) {
  const mainInput = value[group.mainKey] ?? emptyFieldInput();
  const mainActive = mainInput.min || mainInput.max;
  const tierActive = group.tiers.some((t) => {
    const inp = value[t.key];
    return inp && (inp.min || inp.max);
  });

  return (
    <div
      className={`min-w-0 overflow-hidden rounded-md border px-2 py-2 ${
        mainActive || tierActive ? "border-amber-300 bg-amber-50/60" : "border-slate-200 bg-white"
      }`}
    >
      <div className="mb-1.5 flex min-w-0 items-start justify-between gap-2">
        <span className="min-w-0 flex-1 text-[10px] font-semibold leading-snug text-sky-700">
          {group.label}
        </span>
        <div className="flex shrink-0 gap-0.5">
          <input
            type="number"
            value={mainInput.min}
            onChange={(e) => onPatchField(group.mainKey, { min: e.target.value })}
            placeholder="≥"
            title={`${group.label} min`}
            className="w-9 rounded border border-slate-300 bg-white px-0.5 py-0.5 text-center text-[10px] text-slate-900"
          />
          <input
            type="number"
            value={mainInput.max}
            onChange={(e) => onPatchField(group.mainKey, { max: e.target.value })}
            placeholder="≤"
            title={`${group.label} max`}
            className="w-9 rounded border border-slate-300 bg-white px-0.5 py-0.5 text-center text-[10px] text-slate-900"
          />
        </div>
      </div>
      <div className="grid min-w-0 grid-cols-4 gap-1">
        {group.tiers.map((tier) => (
          <TierMiniFilter
            key={tier.key}
            label={tier.label}
            input={value[tier.key] ?? emptyFieldInput()}
            onPatch={(patch) => onPatchField(tier.key, patch)}
          />
        ))}
      </div>
    </div>
  );
}

export function SheetFiltersPanel({
  fields,
  value,
  onChange,
  highlightFno = true,
  onHighlightFnoChange,
}: Props) {
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({
    Rankings: true,
    RSI: true,
    Fusion: true,
  });

  const byGroup = useMemo(() => {
    const map: Record<string, FilterField[]> = {};
    for (const g of SHEET_GROUPS) map[g.key] = [];
    for (const f of fields) {
      if (!map[f.group]) map[f.group] = [];
      map[f.group].push(f);
    }
    return map;
  }, [fields]);

  const activeCount = countActiveFilters(value);
  const fnoOnly = isFnoOnlyFilter(value);
  const ltpMode = ltpFilterMode(value);

  function patchField(key: string, patch: Partial<FieldFilterInput>) {
    onChange({
      ...value,
      [key]: { ...(value[key] ?? emptyFieldInput()), ...patch },
    });
  }

  function toggleGroup(key: string) {
    setOpenGroups((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-slate-500">
          Fill only what you need · <span className="text-slate-400">{activeCount} active</span>
        </p>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => onChange(excelDefaultFilterState(fields))}
            className="rounded border border-slate-300 px-2 py-1 text-[10px] text-slate-400 hover:text-slate-900"
          >
            Excel default
          </button>
          <button
            type="button"
            onClick={() => onChange(emptyFilterState(fields))}
            className="rounded border border-slate-300 px-2 py-1 text-[10px] text-slate-400 hover:text-slate-900"
          >
            Clear all
          </button>
        </div>
      </div>

      <label className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2.5">
        <div>
          <span className="text-xs font-semibold text-slate-700">F&O stocks only</span>
          <p className="text-[10px] text-slate-500">Futures & Options listed equities</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={fnoOnly}
          onClick={() => onChange(setFnoOnlyFilter(value, !fnoOnly))}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
            fnoOnly ? "bg-violet-600" : "bg-slate-300"
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
              fnoOnly ? "translate-x-5" : ""
            }`}
          />
        </button>
      </label>

      <label className="flex cursor-pointer items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2.5">
        <div>
          <span className="text-xs font-semibold text-slate-700">Highlight F&O rows</span>
          <p className="text-[10px] text-slate-500">Violet background + badge on scrip</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={highlightFno}
          onClick={() => onHighlightFnoChange?.()}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${
            highlightFno ? "bg-violet-600" : "bg-slate-300"
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${
              highlightFno ? "translate-x-5" : ""
            }`}
          />
        </button>
      </label>

      <label className="block rounded-lg border border-slate-200 bg-white px-3 py-2.5">
        <span className="text-xs font-semibold text-slate-700">LTP display</span>
        <p className="mb-2 text-[10px] text-slate-500">Blank, zero, or missing live price</p>
        <select
          value={ltpMode}
          onChange={(e) => onChange(setLtpFilterMode(value, e.target.value as "all" | "has" | "missing"))}
          className="w-full rounded border border-slate-300 bg-slate-50 px-2 py-1.5 text-xs text-slate-900"
        >
          <option value="all">Show all stocks</option>
          <option value="has">Only stocks with LTP</option>
          <option value="missing">Only stocks without LTP (blank / 0)</option>
        </select>
      </label>

      {SHEET_GROUPS.map((group) => {
        const groupFields = (byGroup[group.key] ?? []).filter((f) => !FUSION_GROUPED_FIELD_KEYS.has(f.key));
        const fusionGroups = group.key === "Fusion" ? FUSION_SCORE_GROUPS : [];
        if (groupFields.length === 0 && fusionGroups.length === 0) return null;
        const open = openGroups[group.key] ?? false;
        const groupActive =
          groupFields.filter((f) => {
            const inp = value[f.key];
            return inp && (inp.min || inp.max || inp.text.trim() || inp.bool);
          }).length +
          (group.key === "Fusion"
            ? FUSION_SCORE_GROUPS.filter((g) => {
                const keys = [g.mainKey, ...g.tiers.map((t) => t.key)];
                return keys.some((k) => {
                  const inp = value[k];
                  return inp && (inp.min || inp.max);
                });
              }).length
            : 0);

        return (
          <section key={group.key} className="rounded-lg border border-slate-200 bg-slate-50">
            <button
              type="button"
              onClick={() => toggleGroup(group.key)}
              className="flex w-full items-center justify-between px-3 py-2 text-left"
            >
              <span className="text-xs font-semibold text-sky-600/90">{group.label}</span>
              <span className="flex items-center gap-2 text-[10px] text-slate-500">
                {groupActive > 0 && <span className="text-emerald-600">{groupActive} set</span>}
                {open ? "▾" : "▸"}
              </span>
            </button>
            {open && (
              <div className="space-y-0.5 border-t border-slate-200 px-3 pb-3 pt-1">
                {group.key === "Fusion" && (
                  <div className="mb-2 min-w-0 space-y-2">
                    {FUSION_SCORE_GROUPS.map((scoreGroup) => (
                      <FusionScoreGroupRow
                        key={scoreGroup.mainKey}
                        group={scoreGroup}
                        value={value}
                        onPatchField={patchField}
                      />
                    ))}
                  </div>
                )}
                {groupFields.map((field) => (
                  <FieldRow
                    key={field.key}
                    field={field}
                    input={value[field.key] ?? emptyFieldInput()}
                    onPatch={(patch) => patchField(field.key, patch)}
                  />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
