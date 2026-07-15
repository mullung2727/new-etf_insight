import type { Holding } from "@/lib/broker-client";

// kt00018 잔고 stk_cd는 'A' 접두가 붙어 올 수 있어(quotes._strip_code 참고) 6자리로 정규화.
function stripCode(code: string): string {
  const s = code.trim();
  return s[0] === "A" && /^\d+$/.test(s.slice(1)) ? s.slice(1) : s;
}

// 보유목록(acnt_evlt_remn_indv_tot)에서 이 종목 보유분. 미보유/미조회는 null.
export function findHolding(holdings: Holding[] | undefined, code: string): Holding | null {
  if (!holdings) return null;
  return holdings.find((h) => stripCode(h.stk_cd ?? "") === code) ?? null;
}

// 종목 개괄 허브 라우팅 헬퍼. 탭은 URL 쿼리 ?tab= 로 딥링크.
export const STOCK_TABS = ["overview", "chart", "financial", "research", "mentions", "notes"] as const;
export type StockTab = (typeof STOCK_TABS)[number];

// 레거시 탭 → 통합 탭 호환(북마크·외부 링크 방어). telegram/youtube는 mentions로 흡수됨.
const LEGACY_TAB: Record<string, StockTab> = { telegram: "mentions", youtube: "mentions" };

export function resolveTab(raw: string | null | undefined): StockTab {
  if (STOCK_TABS.includes(raw as StockTab)) return raw as StockTab;
  if (raw && raw in LEGACY_TAB) return LEGACY_TAB[raw];
  return "overview";
}

// 허브 링크. overview는 기본이라 tab 생략. date/name 쿼리 보존(차트 기준일·표시명).
export function stockHubHref(
  code: string,
  opts: { tab?: StockTab; date?: string; name?: string } = {},
): string {
  const q = new URLSearchParams();
  if (opts.tab && opts.tab !== "overview") q.set("tab", opts.tab);
  if (opts.date) q.set("date", opts.date);
  if (opts.name) q.set("name", opts.name);
  const qs = q.toString();
  return `/stock/${code}${qs ? `?${qs}` : ""}`;
}
