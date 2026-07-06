import type { FinancialItem, FinancialResponse } from "@/types/dart";

function num(s: string | null | undefined): number | null {
  if (s == null || s === "") return null;
  const n = parseFloat(String(s).replace(/,/g, ""));
  return isNaN(n) ? null : n;
}

function thstrmByKey(r: FinancialResponse): Map<string, number | null> {
  const m = new Map<string, number | null>();
  for (const it of r.list ?? []) m.set(`${it.sj_div}|${it.account_id}`, num(it.thstrm_amount));
  return m;
}

/**
 * Q4 단독값 파생.
 *  - IS/CIS (기간·flow): Q4 = FY − Q1 − Q2 − Q3  (분기 thstrm은 단독값)
 *  - 그 외 BS 등 (시점·stock): FY thstrm = 연말 잔액 = Q4 잔액 → 그대로 통과
 * fy=사업보고서(11011, IS thstrm은 연간누적), q1/q2/q3=분기보고서(11013/12/14).
 * 반환: FY와 동일한 계정 구성의 list, thstrm_amount만 Q4 단독으로 치환.
 *
 * ponytail: 기간항목 매칭은 sj_div|account_id 정확일치. 금융지주처럼 FY는 IS인데
 *  분기는 CIS로 오는 교차 케이스는 null 처리됨 — 필요 시 account_id-only 폴백 추가.
 */
export function deriveQ4List(
  fy: FinancialResponse,
  q1: FinancialResponse,
  q2: FinancialResponse,
  q3: FinancialResponse
): FinancialItem[] {
  const m1 = thstrmByKey(q1), m2 = thstrmByKey(q2), m3 = thstrmByKey(q3);

  return (fy.list ?? []).map(it => {
    const isFlow = it.sj_div === "IS" || it.sj_div === "CIS";
    if (!isFlow) return it; // 시점항목: 연말 잔액 그대로

    const key = `${it.sj_div}|${it.account_id}`;
    const fyv = num(it.thstrm_amount);
    const a = m1.get(key), b = m2.get(key), c = m3.get(key);
    if (fyv == null || a == null || b == null || c == null) {
      return { ...it, thstrm_amount: null };
    }
    return { ...it, thstrm_amount: String(fyv - a - b - c) };
  });
}
