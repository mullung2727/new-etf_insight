"use client";

import { Fragment } from "react";
import { CompareResponse, CompareRow } from "@/types/dart";

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

const SECTION_LABELS: Record<CompareRow["section"], string> = {
  bs: "재무상태표",
  is: "손익계산서",
  ratio: "비율",
};

/** 총계 행 강조 — 비율 기준값이기도 함 */
const TOTAL_KEYS = new Set(["totalAssets", "totalLiab", "totalEquity"]);

/** 인라인 비율 기준값 key 매핑 (총계 행·revenue 행 자체는 null = 비율 미표시) */
function inlineBaseKey(rowKey: string): string | null {
  if (TOTAL_KEYS.has(rowKey)) return null;      // 총계 자체는 미표시
  if (rowKey === "revenue") return null;         // 매출액은 미표시
  if (rowKey.startsWith("bsAsset"))  return "totalAssets";
  if (rowKey.startsWith("bsLiab"))   return "totalLiab";
  if (rowKey.startsWith("equity"))   return "totalEquity";
  if (rowKey === "opProfit" || rowKey === "netIncome") return "revenue";
  return null;
}

export default function CompareTable({ data }: Props) {
  const { corpName, fsDiv, periods, rows } = data;
  const isQuarterly = periods.some(p => /Q/.test(p));

  // 기준값 조회용 맵 (key → values[])
  const baseValMap = new Map(rows.map(r => [r.key, r.values]));

  const sections = (Object.keys(SECTION_LABELS) as CompareRow["section"][])
    .map(section => ({ section, rows: rows.filter(r => r.section === section) }))
    .filter(g => g.rows.length > 0);

  return (
    <div
      className="w-full bg-card border border-fin-gold/25 rounded-[2px] overflow-hidden font-terminal"
      style={{
        boxShadow: "0 0 0 1px rgba(194,239,78,0.08), 0 20px 60px rgba(0,0,0,0.5)",
      }}
    >
      {/* 상단 라임 라인 */}
      <div className="absolute top-0 left-0 right-0 h-[2px]"
        style={{ background: "linear-gradient(90deg, transparent, var(--color-fin-gold) 30%, var(--color-fin-gold) 70%, transparent)" }} />

      {/* 헤더 */}
      <div
        data-testid="compare-header"
        className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-fin-gold/[0.12]"
      >
        <div className="flex items-center gap-3">
          <div className="w-[6px] h-[6px] rounded-full bg-fin-gold" style={{ boxShadow: "0 0 8px var(--color-fin-gold)" }} />
          <span className="text-fin-gold text-fin-base tracking-[0.15em] font-semibold">
            {corpName}
          </span>
          <span className="text-fin-muted text-fin-xs tracking-[0.1em]">·</span>
          <span className="text-fin-label text-fin-xs tracking-[0.15em] uppercase">{isQuarterly ? "분기" : "연간"}</span>
          <span className="text-fin-muted text-fin-xs">·</span>
          <span className="text-fin-label text-fin-xs tracking-[0.15em]">
            {fsDiv === "CFS" ? "연결" : "별도"}
          </span>
        </div>
        <span className="text-fin-muted text-fin-xs tracking-[0.1em]">단위: 억원 / %</span>
      </div>

      {/* 표 */}
      <div className="overflow-x-auto">
        <table
          data-testid="compare-table"
          className="w-full border-collapse text-fin-base"
        >
          <thead>
            <tr className="border-b border-fin-gold/[0.15]">
              <th className="text-left px-6 py-3 text-fin-muted text-fin-xs tracking-[0.15em] uppercase font-normal w-[130px]">
                항목
              </th>
              {periods.map(year => (
                <th
                  key={year}
                  data-year={year}
                  className="text-right px-4 py-3 text-fin-gold/60 text-fin-sm tracking-[0.1em] font-semibold"
                >
                  {year}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sections.map(group => (
              <Fragment key={group.section}>
                <tr
                  data-section-header={group.section}
                  className="border-b border-fin-gold/[0.15] bg-black/[0.25]"
                >
                  <td
                    colSpan={periods.length + 1}
                    className="px-6 py-[8px] text-fin-gold/70 text-fin-xs tracking-[0.2em] font-semibold uppercase"
                  >
                    {SECTION_LABELS[group.section]}
                  </td>
                </tr>
                {group.rows.map((row, idx) => {
                  const isAmount = row.type === "amount";
                  const isTotal = TOTAL_KEYS.has(row.key);
                  const baseKey = inlineBaseKey(row.key);
                  const baseVals = baseKey ? baseValMap.get(baseKey) : null;

                  return (
                    <tr
                      key={row.key}
                      data-row-key={row.key}
                      className={[
                        "border-b border-white/[0.04] transition-colors hover:bg-white/[0.02]",
                        isTotal ? "bg-fin-gold/[0.04]" : idx % 2 === 0 ? "bg-black/[0.08]" : "",
                      ].join(" ")}
                    >
                      <td
                        className={[
                          "px-6 py-[10px] text-fin-sm tracking-[0.05em] whitespace-nowrap",
                          isTotal ? "text-fin-gold font-semibold" : "text-fin-label",
                        ].join(" ")}
                      >
                        {row.label}
                      </td>
                      {row.values.map((val, i) => {
                        const base = baseVals?.[i] ?? null;
                        const pct = (val !== null && base !== null && base !== 0)
                          ? (val / base) * 100 : null;

                        return (
                          <td
                            key={periods[i]}
                            className={[
                              "text-right px-4 py-[10px] tabular-nums tracking-[0.02em]",
                              isTotal ? "font-semibold" : "",
                              val === null
                                ? "text-fin-ghost"
                                : isAmount
                                  ? "text-fin-text"
                                  : "text-fin-gold",
                            ].join(" ")}
                          >
                            {isAmount ? (
                              <>
                                {fmtAmount(val)}
                                {pct !== null && (
                                  <span className="ml-1 text-fin-ratio text-fin-sm font-medium">
                                    ({pct.toFixed(1)}%)
                                  </span>
                                )}
                              </>
                            ) : fmtRatio(val)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
