/**
 * CSS 토큰 → JS 색상값 resolve
 * ECharts 등 canvas 기반 라이브러리는 CSS var 직접 사용 불가 →
 * 런타임에 getComputedStyle로 실제 색상 읽어옴.
 * 스타일 코드는 Tailwind 유틸리티(text-fin-gold 등) 사용.
 */

/** SSR-safe CSS var resolver. 브라우저 환경에서만 실제 값 반환 */
function resolveVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

export function getChartTokens() {
  return {
    gold:    resolveVar("--color-fin-gold",       "#c2ef4e"),  /* lime accent */
    up:      resolveVar("--color-up",             "#ef4444"),
    down:    resolveVar("--color-down",           "#3b82f6"),
    profit:  resolveVar("--color-status-profit",  "#22c55e"),
    surface: resolveVar("--color-card",           "#150f23"),  /* 차트 패널/툴팁 bg */
  };
}

/**
 * 카테고리형 차트 팔레트 — 파이/멀티시리즈용 다색.
 * 브랜드 5색(violet/lime/pink/deep-violet/mid) 우선, 이후 보조 색상.
 * ECharts/Recharts canvas는 리터럴 색이 필요하므로 여기(토큰 정의처)에 hex 유지.
 */
export const CHART_BAR_COLOR = "#6a5fc1"; /* brand violet */
export const CHART_CATEGORICAL = [
  "#6a5fc1", "#c2ef4e", "#fa7faa", "#422082", "#79628c",
  "#f59e0b", "#10b981", "#3b82f6", "#a855f7", "#14b8a6",
  "#f97316", "#84cc16", "#06b6d4", "#f43f5e", "#0ea5e9",
];
