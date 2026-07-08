// 차트 탭 기간선택. 대략적 거래일 수로 최근 구간을 자른다.
export const CHART_RANGES = ["1M", "3M", "6M", "1Y"] as const;
export type ChartRange = (typeof CHART_RANGES)[number];

const TRADING_DAYS: Record<ChartRange, number> = { "1M": 22, "3M": 66, "6M": 126, "1Y": 252 };

export function resolveRange(raw: string | null | undefined): ChartRange {
  return CHART_RANGES.includes(raw as ChartRange) ? (raw as ChartRange) : "3M";
}

// 최신 N거래일만 유지(정렬된 배열 가정). 데이터가 N보다 짧으면 전부.
export function sliceByRange<T>(items: T[], range: ChartRange): T[] {
  const n = TRADING_DAYS[range];
  return items.length > n ? items.slice(-n) : items;
}
