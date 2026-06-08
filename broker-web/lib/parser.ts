import { FinancialItem } from "@/types/dart";

export function parseAmount(raw: string | null | undefined): number | null {
  if (raw == null || raw === "") return null;
  const parsed = parseInt(raw.replace(/,/g, ""), 10);
  return isNaN(parsed) ? null : parsed;
}

export function groupBySjDiv(items: FinancialItem[]): Record<string, FinancialItem[]> {
  return items.reduce<Record<string, FinancialItem[]>>((acc, item) => {
    if (!acc[item.sj_div]) acc[item.sj_div] = [];
    acc[item.sj_div].push(item);
    return acc;
  }, {});
}

const KEY_ACCOUNT_IDS: Record<string, string> = {
  revenue: "ifrs-full_Revenue",
  operatingProfit: "dart_OperatingIncomeLoss",
  netIncome: "ifrs-full_ProfitLoss",
  totalAssets: "ifrs-full_Assets",
};

export interface KeyMetrics {
  revenue: number | null;
  operatingProfit: number | null;
  netIncome: number | null;
  totalAssets: number | null;
}

export function extractKeyMetrics(items: FinancialItem[]): KeyMetrics {
  const metrics: KeyMetrics = {
    revenue: null,
    operatingProfit: null,
    netIncome: null,
    totalAssets: null,
  };

  for (const item of items) {
    for (const [key, accountId] of Object.entries(KEY_ACCOUNT_IDS)) {
      if (item.account_id === accountId) {
        (metrics as unknown as Record<string, number | null>)[key] = parseAmount(item.thstrm_amount);
      }
    }
  }

  return metrics;
}

export interface ChartData {
  years: string[];
  revenue: (number | null)[];
  operatingProfit: (number | null)[];
  netIncome: (number | null)[];
  operatingMargin: (number | null)[];
}

export function buildChartData(
  metricsByYear: { year: string; metrics: KeyMetrics }[]
): ChartData {
  const years = metricsByYear.map((m) => m.year);
  const revenue = metricsByYear.map((m) => m.metrics.revenue);
  const operatingProfit = metricsByYear.map((m) => m.metrics.operatingProfit);
  const netIncome = metricsByYear.map((m) => m.metrics.netIncome);

  const operatingMargin = metricsByYear.map((m) => {
    const r = m.metrics.revenue;
    const op = m.metrics.operatingProfit;
    if (r === null || op === null || r === 0) return null;
    return Math.round((op / r) * 10000) / 100;
  });

  return { years, revenue, operatingProfit, netIncome, operatingMargin };
}
