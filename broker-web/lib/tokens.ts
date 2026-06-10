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
    gold:   resolveVar("--color-fin-gold",       "#F0B429"),
    up:     resolveVar("--color-up",             "#ef4444"),
    down:   resolveVar("--color-down",           "#3b82f6"),
    profit: resolveVar("--color-status-profit",  "#22c55e"),
  };
}
