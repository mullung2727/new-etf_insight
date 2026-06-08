"use client";

import { useState } from "react";
import SearchBar from "@/components/financial/SearchBar";
import FinancialChart from "@/components/financial/FinancialChart";
import FinancialTable from "@/components/financial/FinancialTable";
import { FinancialItem } from "@/types/dart";

interface SearchParams {
  corpCode: string;
  corpName: string;
  year: string;
  reprtCode: string;
  fsDiv: string;
}

export default function Home() {
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<FinancialItem[]>([]);
  const [searchParams, setSearchParams] = useState<SearchParams | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async (params: SearchParams) => {
    setLoading(true);
    setError(null);
    setItems([]);
    setSearchParams(params);

    try {
      const url =
        `/api/financial?corp_code=${params.corpCode}&year=${params.year}` +
        `&reprt=${params.reprtCode}&fs_div=${params.fsDiv}`;
      const res = await fetch(url);
      const data = await res.json();

      if (!res.ok || data.error) {
        throw new Error(data.error ?? "재무제표 조회에 실패했습니다.");
      }

      setItems(data.list ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      {/* 배경 그리드 패턴 */}
      <div className="fixed inset-0 pointer-events-none bg-grid" />

      <div className="relative max-w-[1200px] mx-auto px-6 py-10">
        <section className="mb-6">
          <SearchBar onSearch={handleSearch} loading={loading} />
        </section>

        {error && (
          <div className="mb-6 flex items-center gap-[10px] bg-up/10 border border-up/30 rounded-[2px] px-[18px] py-[14px] text-[#fca5a5] text-xs tracking-[0.05em]">
            <span className="text-up text-sm">✕</span>
            {error}
          </div>
        )}

        {loading && (
          <div className="mb-6 flex items-center gap-[10px] bg-primary/[0.06] border border-primary/20 rounded-[2px] px-[18px] py-[14px] text-primary/70 text-[11px] tracking-[0.15em]">
            <span style={{ animation: "pulse 1s ease-in-out infinite", display: "inline-block" }}>
              ◆
            </span>
            <style>{`@keyframes pulse{0%,100%{opacity:0.3}50%{opacity:1}}`}</style>
            DART API 호출 중...
          </div>
        )}

        {(items.length > 0 || (!loading && searchParams)) && (
          <section className="mb-6">
            <FinancialChart
              items={items}
              year={searchParams?.year ?? ""}
              corpName={searchParams?.corpName ?? ""}
            />
          </section>
        )}

        {(items.length > 0 || (!loading && searchParams)) && (
          <section>
            <FinancialTable items={items} />
          </section>
        )}

        {!loading && !searchParams && !error && (
          <div className="text-center px-6 py-20 text-white/10">
            <div className="text-5xl mb-4 opacity-30">▭</div>
            <p className="text-[11px] tracking-[0.2em] uppercase">
              기업을 검색하면 재무제표가 표시됩니다
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
