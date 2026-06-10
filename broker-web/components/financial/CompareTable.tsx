"use client";

import { CompareResponse } from "@/types/dart";

interface Props {
  data: CompareResponse;
}

function fmtAmount(val: number | null): string {
  if (val === null) return "—";
  const abs = Math.abs(val);
  const sign = val < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}${(abs / 1e12).toFixed(1)}조`;
  if (abs >= 1e8) return `${sign}${Math.round(abs / 1e8).toLocaleString()}억`;
  return `${sign}${Math.round(abs / 1e4).toLocaleString()}만`;
}

function fmtRatio(val: number | null): string {
  if (val === null) return "—";
  return `${val.toFixed(1)}%`;
}

const AMOUNT_KEYS = new Set(["revenue", "opProfit", "netIncome", "totalAssets", "totalLiab", "totalEquity"]);

export default function CompareTable({ data }: Props) {
  const { corpName, fsDiv, periods, rows } = data;

  return (
    <div
      className="w-full border border-primary/25 rounded-[2px] overflow-hidden font-terminal"
      style={{
        background: "linear-gradient(135deg, #0A1628 0%, #0f1f3d 50%, #0A1628 100%)",
        boxShadow: "0 0 0 1px rgba(240,180,41,0.08), 0 20px 60px rgba(0,0,0,0.5)",
      }}
    >
      {/* 상단 골드 라인 */}
      <div className="absolute top-0 left-0 right-0 h-[2px]"
        style={{ background: "linear-gradient(90deg, transparent, #F0B429 30%, #F0B429 70%, transparent)" }} />

      {/* 헤더 */}
      <div
        data-testid="compare-header"
        className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-primary/[0.12]"
      >
        <div className="flex items-center gap-3">
          <div className="w-[6px] h-[6px] rounded-full bg-primary" style={{ boxShadow: "0 0 8px #F0B429" }} />
          <span className="text-primary text-[11px] tracking-[0.15em] font-semibold">
            {corpName}
          </span>
          <span className="text-white/30 text-[9px] tracking-[0.1em]">·</span>
          <span className="text-white/40 text-[9px] tracking-[0.15em] uppercase">연간</span>
          <span className="text-white/30 text-[9px]">·</span>
          <span className="text-white/40 text-[9px] tracking-[0.15em]">
            {fsDiv === "CFS" ? "연결" : "별도"}
          </span>
        </div>
        <span className="text-white/20 text-[9px] tracking-[0.1em]">단위: 억원 / %</span>
      </div>

      {/* 표 */}
      <div className="overflow-x-auto">
        <table
          data-testid="compare-table"
          className="w-full border-collapse text-[11px]"
        >
          <thead>
            <tr className="border-b border-primary/[0.15]">
              <th className="text-left px-6 py-3 text-white/30 text-[9px] tracking-[0.15em] uppercase font-normal w-[130px]">
                항목
              </th>
              {periods.map(year => (
                <th
                  key={year}
                  data-year={year}
                  className="text-right px-4 py-3 text-primary/60 text-[10px] tracking-[0.1em] font-semibold"
                >
                  {year}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => {
              const isAmount = AMOUNT_KEYS.has(row.key);
              const isSectionBorder = row.key === "opMargin"; // 비율 첫 행 위에 구분선

              return (
                <tr
                  key={row.key}
                  data-row-key={row.key}
                  className={[
                    "border-b border-white/[0.04] transition-colors hover:bg-white/[0.02]",
                    isSectionBorder ? "border-t border-primary/[0.12]" : "",
                    idx % 2 === 0 ? "bg-black/[0.08]" : "",
                  ].join(" ")}
                >
                  <td className="px-6 py-[10px] text-white/55 text-[10px] tracking-[0.05em] whitespace-nowrap">
                    {row.label}
                  </td>
                  {row.values.map((val, i) => (
                    <td
                      key={periods[i]}
                      className={[
                        "text-right px-4 py-[10px] tabular-nums tracking-[0.02em]",
                        val === null
                          ? "text-white/15"
                          : isAmount
                            ? "text-white/80"
                            : "text-primary/80",
                      ].join(" ")}
                    >
                      {isAmount ? fmtAmount(val) : fmtRatio(val)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
