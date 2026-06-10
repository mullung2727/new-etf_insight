import { FinancialResponse } from "@/types/dart";

/**
 * 재무상태표 회사별 주요 계정(top-N) 선정 — docs/PLAN_FINANCIAL_BS_TOPN.md
 * 실호출 6개사(삼성전자·신한지주·현대차·카카오·M83·삼성생명) 검증 (2026-06-10)
 */

type BsItem = FinancialResponse["list"][number];

export type AmountField = "thstrm" | "frmtrm" | "bfefrmtrm";

/** 동적 행 정의 — nm이 표시 레이블이자 연도 간 매칭 키 (비표준 코드는 id 매칭 불가) */
export type BsRowDef = { nm: string; ids: string[] };

const NONSTANDARD_ID = "-표준계정코드 미사용-";

/** 표준 소계·총계 — top-N 후보 제외 (이중합산 방지) */
const SUBTOTAL_IDS = new Set([
  "ifrs-full_Assets",
  "ifrs-full_CurrentAssets",
  "ifrs-full_NoncurrentAssets",
  "ifrs-full_Liabilities",
  "ifrs-full_CurrentLiabilities",
  "ifrs-full_NoncurrentLiabilities",
  "ifrs-full_Equity",
  "ifrs-full_EquityAndLiabilities",
]);

function parseAmount(s: string | null | undefined): number | null {
  if (!s) return null;
  const n = parseFloat(String(s).replace(/,/g, ""));
  return isNaN(n) ? null : n;
}

type OrdItem = BsItem & { ordNum: number; value: number | null };

/**
 * BS 항목을 자산/부채 섹션으로 분리.
 * ord가 섹션별 연속 구간이고 구간 사이 빈 번호가 경계 — 총계 계정이 구간 중간에
 * 섞이므로(알파벳 정렬) "총계 포함 구간 = 해당 섹션"으로 판별.
 */
function splitSections(list: FinancialResponse["list"]): {
  assets: OrdItem[];
  liabilities: OrdItem[];
} {
  const bs: OrdItem[] = list
    .filter(i => i.sj_div === "BS")
    .map(i => ({ ...i, ordNum: parseInt(i.ord, 10), value: parseAmount(i.thstrm_amount) }))
    .sort((a, b) => a.ordNum - b.ordNum);

  const runs: OrdItem[][] = [];
  let cur: OrdItem[] = [];
  for (const item of bs) {
    if (cur.length && item.ordNum > cur[cur.length - 1].ordNum + 1) {
      runs.push(cur);
      cur = [];
    }
    cur.push(item);
  }
  if (cur.length) runs.push(cur);

  const assets = runs.find(r => r.some(i => i.account_id === "ifrs-full_Assets"));
  const liabilities = runs.find(r => r.some(i => i.account_id === "ifrs-full_Liabilities"));
  // 섹션 분리 실패(자산·부채가 한 구간) 시 빈 배열 — 총계 행만 표시되도록 우아한 축소
  if (!assets || !liabilities || assets === liabilities) return { assets: [], liabilities: [] };
  return { assets, liabilities };
}

/**
 * 표준 부모 + 비표준 자식 이중공시 제거 (삼성생명 보험계약부채 케이스).
 * 표준 항목 직후 ord 연속 비표준 항목들의 합이 부모와 ±0.1% 이내면 자식으로
 * 판정해 제외하고 부모 유지.
 */
function dropNonstandardChildren(items: OrdItem[]): OrdItem[] {
  const removed = new Set<number>();
  for (let i = 0; i < items.length; i++) {
    const parent = items[i];
    if (parent.account_id === NONSTANDARD_ID || parent.value == null) continue;
    const children: number[] = [];
    for (
      let j = i + 1;
      j < items.length &&
      items[j].account_id === NONSTANDARD_ID &&
      items[j].ordNum === parent.ordNum + (j - i);
      j++
    ) {
      children.push(j);
    }
    if (!children.length) continue;
    const sum = children.reduce((s, j) => s + (items[j].value ?? 0), 0);
    if (Math.abs(sum - parent.value) <= Math.abs(parent.value) * 0.001) {
      children.forEach(j => removed.add(j));
    }
  }
  return items.filter((_, idx) => !removed.has(idx));
}

/** 동일 account_nm 합산 (현대차 금융업채권 유동+비유동, 기타자산 쌍 등) */
function mergeByName(items: OrdItem[]): { nm: string; ids: string[]; value: number }[] {
  const map = new Map<string, { nm: string; ids: string[]; value: number }>();
  for (const i of items) {
    if (i.value == null) continue;
    const e = map.get(i.account_nm);
    if (e) {
      e.ids.push(i.account_id);
      e.value += i.value;
    } else {
      map.set(i.account_nm, { nm: i.account_nm, ids: [i.account_id], value: i.value });
    }
  }
  return [...map.values()];
}

/** 최신 연도 응답에서 자산 top-N / 부채 top-N 행 정의 선정 */
export function selectBsTopAccounts(
  list: FinancialResponse["list"],
  assetN = 5,
  liabN = 3
): { assets: BsRowDef[]; liabilities: BsRowDef[] } {
  const sections = splitSections(list);
  const pick = (items: OrdItem[], n: number): BsRowDef[] =>
    mergeByName(dropNonstandardChildren(items.filter(i => !SUBTOTAL_IDS.has(i.account_id))))
      .sort((a, b) => b.value - a.value)
      .slice(0, n)
      .map(({ nm, ids }) => ({ nm, ids }));
  return {
    assets: pick(sections.assets, assetN),
    liabilities: pick(sections.liabilities, liabN),
  };
}

/**
 * 행 정의의 금액 추출 — account_nm 매칭 합산.
 * field로 전기(frmtrm)/전전기(bfefrmtrm) 추출 시 백필 donor 응답에도 사용 가능.
 */
export function extractBsRowAmount(
  list: FinancialResponse["list"],
  row: BsRowDef,
  field: AmountField = "thstrm"
): number | null {
  const vals = list
    .filter(i => i.sj_div === "BS" && i.account_nm === row.nm)
    .map(i => parseAmount(i[`${field}_amount`]))
    .filter((v): v is number => v !== null);
  if (!vals.length) return null;
  return vals.reduce((s, v) => s + v, 0);
}
