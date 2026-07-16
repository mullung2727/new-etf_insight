import { Suspense } from "react";
import Link from "next/link";
import { brokerClient } from "@/lib/broker-client";
import { resolveTab, stockHubHref, findHolding, STOCK_TABS, type StockTab } from "@/lib/stock-hub";
import { resolveRange } from "@/lib/chart-range";
import type { Holding } from "@/lib/broker-client";
import StockSearch from "@/components/stock/stock-search";
import ChartTab from "@/components/stock/chart-tab";
import FinancialTab from "@/components/stock/financial-tab";
import FinancialModeToggle from "@/components/stock/financial-mode-toggle";
import ResearchTab from "@/components/stock/research-tab";
import MentionsTab from "@/components/stock/mentions-tab";
import NotesTab from "@/components/stock/notes-tab";
import OverviewTab from "@/components/stock/overview-tab";

const TAB_LABEL: Record<StockTab, string> = {
  overview: "개요",
  chart: "차트",
  financial: "재무",
  research: "리포트",
  mentions: "언급",
  notes: "노트",
};

function yyyymmdd(date: Date): string {
  return date.toISOString().slice(0, 10).replace(/-/g, "");
}

async function fetchPrice(code: string): Promise<number | null> {
  try {
    return (await brokerClient.getQuote(code)).price;
  } catch {
    return null; // 헤더 현재가 실패는 치명적 아님
  }
}

async function fetchHolding(code: string): Promise<Holding | null> {
  try {
    const bal = await brokerClient.getBalance();
    return findHolding(bal.acnt_evlt_remn_indv_tot, code);
  } catch {
    return null; // 미보유/조회실패 동일 취급
  }
}

export default async function StockHubPage({
  params,
  searchParams,
}: {
  params: Promise<{ code: string }>;
  searchParams: Promise<{
    tab?: string; date?: string; name?: string; range?: string; tg_session?: string; fin_mode?: string;
  }>;
}) {
  const { code } = await params;
  const { tab: tabRaw, date, name, range: rangeRaw, tg_session, fin_mode } = await searchParams;
  const tab = resolveTab(tabRaw);
  const finMode = fin_mode === "quarterly" ? "quarterly" : "annual";
  const baseDate = date ?? yyyymmdd(new Date());

  const [price, holding] = await Promise.all([fetchPrice(code), fetchHolding(code)]);
  const prftRate = holding ? Number(holding.prft_rt) : null;

  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      <div className="fixed inset-0 pointer-events-none bg-grid" />

      <div className="relative max-w-[1200px] mx-auto px-6 py-10">
        {/* 검색바 유지 — 허브에서도 다른 종목 바로 검색 */}
        <div className="mb-6">
          <StockSearch />
        </div>

        {/* 공통 헤더 */}
        <div className="mt-6 mb-6">
          <div className="flex items-baseline gap-3">
            <h1 className="text-2xl font-bold text-primary tracking-[0.05em]">{name ?? code}</h1>
            <span className="text-xs text-white/50">{code}</span>
            {price != null && (
              <span className="text-lg text-white/80">{price.toLocaleString()}</span>
            )}
            {prftRate != null && Number.isFinite(prftRate) && (
              <span
                className={
                  "text-sm font-bold px-2 py-0.5 rounded border " +
                  (prftRate > 0
                    ? "text-up border-up/30"
                    : prftRate < 0
                    ? "text-down border-down/30"
                    : "text-white/60 border-white/20")
                }
              >
                {prftRate > 0 ? "+" : ""}{prftRate.toFixed(2)}%
              </span>
            )}
          </div>
        </div>

        {/* 탭 네비 (URL 딥링크) */}
        <nav className="flex gap-1 border-b border-primary/20 mb-8">
          {STOCK_TABS.map((t) => (
            <Link
              key={t}
              href={stockHubHref(code, { tab: t, date, name })}
              className={
                "px-4 py-2 text-[13px] tracking-[0.1em] border-b-2 -mb-px transition-colors " +
                (t === tab
                  ? "border-primary text-primary"
                  : "border-transparent text-white/40 hover:text-white/70")
              }
            >
              {TAB_LABEL[t]}
            </Link>
          ))}
        </nav>

        {/* 활성 탭 */}
        {tab === "chart" ? (
          <ChartTab code={code} baseDate={baseDate} name={name} range={resolveRange(rangeRaw)} />
        ) : tab === "financial" ? (
          <>
            <Suspense fallback={<div className="mb-6 h-9" />}>
              <FinancialModeToggle mode={finMode} />
            </Suspense>
            <FinancialTab code={code} mode={finMode} />
          </>
        ) : tab === "research" ? (
          <ResearchTab key={code} code={code} name={name ?? code} />
        ) : tab === "mentions" ? (
          <MentionsTab key={code} code={code} session={tg_session ?? ""} />
        ) : tab === "notes" ? (
          <NotesTab code={code} holding={holding} />
        ) : tab === "overview" ? (
          <OverviewTab code={code} holding={holding} />
        ) : (
          <div className="text-center py-20 text-[11px] tracking-[0.2em] text-white/15">
            {TAB_LABEL[tab]} 탭 — 준비 중
          </div>
        )}
      </div>
    </div>
  );
}
