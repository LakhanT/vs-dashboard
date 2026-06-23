import { isPercentRatioColumn, ratioToDisplayPercent } from "./percentUtils";

export type DashboardRow = Record<string, string | number | boolean | null | undefined>;

function exportCellValue(col: string, value: unknown): string | number | boolean {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (isPercentRatioColumn(col)) return ratioToDisplayPercent(value);
    return value;
  }
  return String(value);
}

export function dashboardExportFilename(ext: "csv" | "xlsx", asOf?: string | null): string {
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  const datePart = asOf ? `-${asOf}` : "";
  return `vs-dashboard${datePart}-${stamp}.${ext}`;
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function downloadDashboardCsv(
  columns: string[],
  rows: DashboardRow[],
  labelFor: (col: string) => string,
  filename: string,
) {
  if (!rows.length) return;

  const headers = columns.map(labelFor);
  const lines = [
    headers.map(escapeCsvCell).join(","),
    ...rows.map((row) =>
      columns.map((col) => escapeCsvCell(exportCellValue(col, row[col]))).join(","),
    ),
  ];

  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  triggerDownload(blob, filename);
}

function escapeCsvCell(value: string | number | boolean): string {
  const text = String(value);
  if (/[",\r\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

export async function downloadDashboardXlsx(
  columns: string[],
  rows: DashboardRow[],
  labelFor: (col: string) => string,
  filename: string,
) {
  if (!rows.length) return;

  const XLSX = await import("xlsx");
  const headers = columns.map(labelFor);
  const sheetRows = rows.map((row) => {
    const out: Record<string, string | number | boolean> = {};
    for (const col of columns) {
      out[labelFor(col)] = exportCellValue(col, row[col]);
    }
    return out;
  });

  const worksheet = XLSX.utils.json_to_sheet(sheetRows, { header: headers });
  const workbook = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(workbook, worksheet, "Dashboard");
  const buffer = XLSX.write(workbook, { bookType: "xlsx", type: "array" });
  const blob = new Blob([buffer], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  triggerDownload(blob, filename);
}
