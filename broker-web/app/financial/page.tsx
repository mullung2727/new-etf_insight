"use client";

import { useState, useCallback } from "react";
import CompareSearchBar from "@/components/financial/CompareSearchBar";
import CompareTable from "@/components/financial/CompareTable";
import { CompareResponse } from "@/types/dart";

type Mode = "annual" | "quarterly";

export default function FinancialPage() {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("annual");
  const [lastCorp, setLastCorp] = useState<string | null>(null);

  const load = useCallback(async (corpCode: string, m: Mode) => {
    setLoading(true);
    setError(null);
    setData(null);
    try {
      const url = `/api/financial/compare?corp_code=${corpCode}${m === "quarterly" ? "&mode=quarterly" : ""}`;
      const res = await fetch(url);
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error ?? "조회 실패");
      setData(json);
    } catch (err) {
      setError(err instanceof Error ? err.message : "알 수 없는 오류");
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSearch = useCallback((corpCode: string) => {
    setLastCorp(corpCode);
    load(corpCode, mode);
  }, [load, mode]);

  const handleModeChange = useCallback((m: Mode) => {
    setMode(m);
    if (lastCorp) load(lastCorp, m);
  }, [load, lastCorp]);

  return (
    <div className="fin-scope min-h-screen bg-background font-terminal">
      <div className="fixed inset-0 pointer-events-none bg-grid" />

      <div className="relative max-w-[1200px] mx-auto px-6 py-10">
        <section className="mb-6">
          <CompareSearchBar onSearch={handleSearch} loading={loading} />
        </section>

        <section className="mb-6 flex items-center gap-2" data-testid="mode-toggle">
          {(["annual", "quarterly"] as const).map(m => (
            <button
              key={m}
              data-testid={`mode-${m}`}
              onClick={() => handleModeChange(m)}
              disabled={loading}
              aria-pressed={mode === m}
              className={[
                "px-4 py-[6px] text-fin-xs tracking-[0.15em] uppercase rounded-[2px] border transition-colors",
                mode === m
                  ? "border-fin-gold/60 text-fin-gold bg-fin-gold/[0.08]"
                  : "border-fin-gold/15 text-fin-muted hover:text-fin-label hover:border-fin-gold/30",
              ].join(" ")}
            >
              {m === "annual" ? "연간 5년" : "분기 8개"}
            </button>
          ))}
        </section>

        {error && (
          <div className="mb-6 flex items-center gap-[10px] bg-up/10 border border-up/30 rounded-[2px] px-[18px] py-[14px] text-[#fca5a5] text-xs tracking-[0.05em]">
            <span className="text-up text-sm">✕</span>
            {error}
          </div>
        )}

        {loading && (
          <div className="mb-6 flex items-center gap-[10px] bg-fin-gold/[0.06] border border-fin-gold/20 rounded-[2px] px-[18px] py-[14px] text-fin-gold/70 text-fin-base tracking-[0.15em]">
            <span style={{ animation: "pulse 1s ease-in-out infinite", display: "inline-block" }}>◆</span>
            <style>{`@keyframes pulse{0%,100%{opacity:0.3}50%{opacity:1}}`}</style>
            DART API 호출 중 (~14회 병렬)...
          </div>
        )}

        {data && (
          <section className="relative">
            <CompareTable data={data} />
          </section>
        )}

        {!loading && !data && !error && (
          <div className="text-center px-6 py-20 text-white/10">
            <div className="text-5xl mb-4 opacity-30">▭</div>
            <p className="text-fin-base tracking-[0.2em] uppercase">
              기업을 검색하면 다기간 재무 비교가 표시됩니다
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
