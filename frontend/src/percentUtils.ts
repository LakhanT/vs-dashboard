/** Percent fields are stored as ratios: 0.05 = 5%, 2.35 = 235%. */

export function isPercentRatioColumn(key: string): boolean {
  return (
    key.includes("pct") ||
    key.includes("retracement") ||
    key.includes("green") ||
    key.includes("rise") ||
    key.includes("bullish")
  );
}

export function ratioToDisplayPercent(value: number): number {
  return value * 100;
}

/** Convert user input (same number shown in the table, e.g. 235 or 2.35) to stored ratio. */
export function displayPercentToRatio(value: number): number {
  return value / 100;
}

export function formatPercentRatio(value: number, digits = 2): string {
  return `${ratioToDisplayPercent(value).toFixed(digits)}%`;
}
