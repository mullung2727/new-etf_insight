export function formatKrw(value: number | null | undefined): string {
  if (value == null) return "-";
  return "₩" + new Intl.NumberFormat("ko-KR").format(value);
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null) return "-";
  return new Intl.NumberFormat("ko-KR").format(value);
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

// --- ETF formatters ---

export function formatDate(s: string | null): string {
  if (!s || s.length !== 8) return s ?? "-";
  return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`;
}

const THEME_STATUS_LABELS: Record<string, string> = {
  theme: "테마형", mixed: "혼합형", non_theme: "비테마형", unknown: "미분류",
};
const THEME_BUCKET_LABELS: Record<string, string> = {
  technology: "기술", energy_materials: "에너지·소재", healthcare: "헬스케어",
  industrial_defense: "산업·방산", consumer_demographic: "소비·인구",
  finance_income: "금융·인컴", country_macro: "국가·매크로",
  digital_asset: "디지털자산", none: "없음", unknown: "미분류",
};

export const formatThemeStatus = (s: string | null) => s ? (THEME_STATUS_LABELS[s] ?? s) : "-";
export const formatThemeBucket = (s: string | null) => s ? (THEME_BUCKET_LABELS[s] ?? s) : "-";
export const THEME_STATUSES = Object.entries(THEME_STATUS_LABELS).map(([value, label]) => ({ value, label }));
export const THEME_BUCKETS = Object.entries(THEME_BUCKET_LABELS).map(([value, label]) => ({ value, label }));

// --- Trading formatters ---

export function parseKrwString(raw: string | null | undefined): number | null {
  if (!raw) return null;
  const n = parseInt(raw.replace(/[^0-9-]/g, ""), 10);
  return isNaN(n) ? null : n;
}
