"use client";

import { useState } from "react";
import { FinancialItem } from "@/types/dart";
import { groupBySjDiv, parseAmount } from "@/lib/parser";

interface FinancialTableProps {
  items: FinancialItem[];
}

const TABS = [
  { key: "BS", label: "재무상태표", abbr: "B/S" },
  { key: "IS", label: "손익계산서", abbr: "I/S" },
  { key: "CF", label: "현금흐름표", abbr: "C/F" },
  { key: "CIS", label: "포괄손익계산서", abbr: "CIS" },
];

function formatAmount(raw: string | null): { text: string; negative: boolean } {
  const amount = parseAmount(raw);
  if (amount === null) return { text: "—", negative: false };
  const negative = amount < 0;
  const abs = Math.abs(amount);
  const formatted = abs.toLocaleString("ko-KR");
  return { text: negative ? `(${formatted})` : formatted, negative };
}

export default function FinancialTable({ items }: FinancialTableProps) {
  const [activeTab, setActiveTab] = useState("BS");
  const grouped = groupBySjDiv(items);
  const tabItems = grouped[activeTab] ?? [];
  const availableTabs = TABS.filter((t) => grouped[t.key] && grouped[t.key].length > 0);

  if (items.length === 0) {
    return (
      <div className="bg-[#0A1628]/80 border border-fin-gold/[0.12] rounded-[2px] py-12 px-6 text-center font-terminal">
        <div className="text-fin-gold/30 text-[32px] mb-3">▭</div>
        <p className="text-fin-muted text-fin-base tracking-[0.15em]">
          NO DATA — 기업을 검색하세요
        </p>
      </div>
    );
  }

  return (
    <div
      className="border border-fin-gold/20 rounded-[2px] font-terminal overflow-hidden"
      style={{ background: "linear-gradient(135deg, #0A1628 0%, #0f1f3d 100%)" }}
    >
      {/* 상단 골드 라인 */}
      <div
        className="h-[2px]"
        style={{
          background: "linear-gradient(90deg, transparent, #F0B429 30%, #F0B429 70%, transparent)",
        }}
      />

      {/* 탭 헤더 */}
      <div className="flex border-b border-fin-gold/[0.12] bg-black/20">
        {availableTabs.map((tab) => {
          const active = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={[
                "px-5 py-3 border-b-2 text-fin-sm tracking-[0.15em] uppercase cursor-pointer transition-all duration-150 font-[inherit]",
                active
                  ? "bg-fin-gold/10 border-fin-gold text-fin-gold font-bold"
                  : "bg-transparent border-transparent text-white/35 font-normal",
              ].join(" ")}
            >
              {tab.abbr}
              <span className="block text-[8px] mt-[1px] tracking-[0.1em] opacity-60">
                {tab.label}
              </span>
            </button>
          );
        })}
      </div>

      {/* 테이블 */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-black/30">
              <th className="p-[10px_16px] text-left text-fin-gold/50 text-fin-xs tracking-[0.2em] font-semibold uppercase border-b border-fin-gold/10 min-w-[200px]">
                계정명
              </th>
              {["당기", "전기", "전전기"].map((label) => (
                <th
                  key={label}
                  className="p-[10px_16px] text-right text-fin-gold/50 text-fin-xs tracking-[0.2em] font-semibold uppercase border-b border-fin-gold/10 min-w-[140px]"
                >
                  {label}
                  <span className="block text-[8px] opacity-50 mt-[1px]">(원)</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tabItems.map((item, i) => {
              const current = formatAmount(item.thstrm_amount);
              const prev = formatAmount(item.frmtrm_amount);
              const prevPrev = formatAmount(item.bfefrmtrm_amount);
              return (
                <tr
                  key={`${item.account_id}-${i}`}
                  className={[
                    "border-b border-white/[0.04] transition-colors duration-100 hover:bg-fin-gold/[0.05]",
                    i % 2 === 0 ? "bg-transparent" : "bg-white/[0.015]",
                  ].join(" ")}
                >
                  <td className="px-4 py-[9px] text-white/75 text-xs tracking-[0.01em]">
                    {item.account_nm}
                  </td>
                  {[current, prev, prevPrev].map((val, j) => (
                    <td
                      key={j}
                      className={[
                        "px-4 py-[9px] text-right text-xs tracking-[0.03em] tabular-nums",
                        val.negative
                          ? "text-up"
                          : val.text === "—"
                          ? "text-fin-muted"
                          : "text-white/85",
                      ].join(" ")}
                    >
                      {val.text}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* 하단 메타 */}
      <div className="px-4 py-2 border-t border-fin-gold/[0.08] bg-black/20 flex justify-between items-center">
        <span className="text-fin-muted text-fin-xs tracking-[0.1em]">
          {tabItems.length} ROWS · KRW
        </span>
        <span className="text-fin-gold/30 text-fin-xs tracking-[0.1em]">SOURCE: DART OpenAPI</span>
      </div>
    </div>
  );
}
