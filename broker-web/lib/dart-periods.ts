import type { ReprtCode } from "@/types/dart";

export type CompareMode = "annual" | "quarterly";
export type Quarter = 1 | 2 | 3 | 4;

export interface PeriodDesc {
  /** 표시 라벨 — "2024" (annual) | "2024Q3" (quarterly) */
  label: string;
  year: number;
  reprtCode: ReprtCode;
  /** quarterly 전용. Q4는 reprtCode 11011(연간누적)이며
   *  실제 단독값 = FY − (Q1+Q2+Q3) 로 fetch 단에서 계산 (Step 2). */
  quarter?: Quarter;
}

/** 분기 → DART reprt_code. Q4는 사업보고서(연간)를 받아 차감 계산 대상. */
export const QUARTER_REPRT: Record<Quarter, ReprtCode> = {
  1: "11013",
  2: "11012",
  3: "11014",
  4: "11011",
};

/**
 * 최신 기준점에서 과거로 count개 기간을 만들어 오름차순(과거→현재)으로 반환.
 * - annual:    base.year 기준 [base-count+1 .. base] 연도, reprt 11011.
 * - quarterly: base.{year,quarter} 기준 count 분기 역순 나열 후 오름차순.
 */
export function buildPeriods(
  mode: CompareMode,
  count: number,
  base: { year: number; quarter?: Quarter }
): PeriodDesc[] {
  if (mode === "annual") {
    return Array.from({ length: count }, (_, i) => {
      const year = base.year - count + 1 + i;
      return { label: String(year), year, reprtCode: "11011" as ReprtCode };
    });
  }

  let y = base.year;
  let q: Quarter = base.quarter ?? 4;
  const out: PeriodDesc[] = [];
  for (let i = 0; i < count; i++) {
    out.push({ label: `${y}Q${q}`, year: y, quarter: q, reprtCode: QUARTER_REPRT[q] });
    if (q === 1) { q = 4; y -= 1; } else { q = (q - 1) as Quarter; }
  }
  return out.reverse();
}
