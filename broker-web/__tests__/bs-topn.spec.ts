import { test, expect } from "@playwright/test";
import { readFileSync } from "fs";
import path from "path";
import { selectBsTopAccounts, extractBsRowAmount } from "../lib/dart-bs-topn";
import type { FinancialResponse } from "../types/dart";

// 실응답 fixture (2025 CFS, 2026-06-10 수집) 기반 단위 테스트 — 서버 불필요
function loadList(name: string): FinancialResponse["list"] {
  const p = path.join(__dirname, "fixtures", `acnt-${name}-2025-cfs.json`);
  return (JSON.parse(readFileSync(p, "utf-8")) as FinancialResponse).list;
}

const num = (s: string | null) => parseFloat(String(s).replace(/,/g, ""));
const totalOf = (list: FinancialResponse["list"], id: string) =>
  num(list.find(i => i.account_id === id && i.sj_div === "BS")!.thstrm_amount);

// ── 삼성전자 — 유동/비유동 소계 제외, 세부 계정만 top-N ─────────────────────
test("삼성전자 — 자산 top5 계정명·값 (소계 제외)", () => {
  const list = loadList("samsung");
  const { assets } = selectBsTopAccounts(list);
  expect(assets.map(r => r.nm)).toEqual([
    "유형자산", "단기금융상품", "현금및현금성자산", "재고자산", "매출채권",
  ]);
  expect(extractBsRowAmount(list, assets[0])).toBe(215_304_784_000_000);
  expect(extractBsRowAmount(list, assets[4])).toBe(51_127_642_000_000);
});

test("삼성전자 — 부채 top3 계정명", () => {
  const list = loadList("samsung");
  const { liabilities } = selectBsTopAccounts(list);
  expect(liabilities.map(r => r.nm)).toEqual(["미지급비용", "미지급금", "단기차입금"]);
});

test("삼성전자 — frmtrm(전기) 추출", () => {
  const list = loadList("samsung");
  const { assets } = selectBsTopAccounts(list);
  expect(extractBsRowAmount(list, assets[0], "frmtrm")).toBe(205_945_209_000_000);
});

// ── 현대차 — 동일 계정명(금융업채권 유동+비유동) 합산 ─────────────────────
test("현대차 — 금융업채권 합산 1행 (비표준 코드 2건)", () => {
  const list = loadList("hyundai");
  const { assets } = selectBsTopAccounts(list);
  const fin = assets.find(r => r.nm === "금융업채권");
  expect(fin).toBeDefined();
  expect(fin!.ids).toHaveLength(2);
  expect(extractBsRowAmount(list, fin!)).toBe(134_350_592_000_000); // 55.81조 + 78.54조
  // 합산 후 같은 이름이 top에 중복 등장하지 않음
  expect(assets.filter(r => r.nm === "금융업채권")).toHaveLength(1);
});

// ── 삼성생명 — 표준 부모 + 비표준 자식 이중공시 → 자식 제외 ────────────────
test("삼성생명 — 부채 top3 = 부모 유지, 비표준 자식 제외", () => {
  const list = loadList("samsunglife");
  const { liabilities } = selectBsTopAccounts(list);
  expect(liabilities.map(r => r.nm)).toEqual(["보험계약부채", "투자계약부채", "차입부채"]);
  // 자식(무배당/유배당/변액)은 후보에서 제외됨
  for (const child of ["무배당보험계약부채", "유배당보험계약부채", "변액보험계약부채"]) {
    expect(liabilities.find(r => r.nm === child), child).toBeUndefined();
  }
  expect(extractBsRowAmount(list, liabilities[0])).toBe(202_007_098_000_000);
});

// ── 신한지주 — flat 금융 구조 ────────────────────────────────────────────
test("신한지주 — 자산 top1 = 상각후원가측정대출채권", () => {
  const list = loadList("shinhan");
  const { assets } = selectBsTopAccounts(list);
  expect(assets[0].nm).toBe("상각후원가측정대출채권");
  expect(extractBsRowAmount(list, assets[0])).toBe(464_773_880_000_000);
  expect(extractBsRowAmount(list, assets[0], "frmtrm")).toBe(449_295_238_000_000);
});

// ── 전 종목 invariant — 기타 행 음수 없음 ─────────────────────────────────
for (const name of ["samsung", "shinhan", "hyundai", "kakao", "m83", "samsunglife"]) {
  test(`${name} — 기타자산·기타부채 ≥ 0`, () => {
    const list = loadList(name);
    const { assets, liabilities } = selectBsTopAccounts(list);
    expect(assets).toHaveLength(5);
    expect(liabilities).toHaveLength(3);

    const assetTotal = totalOf(list, "ifrs-full_Assets");
    const liabTotal = totalOf(list, "ifrs-full_Liabilities");
    const assetTop = assets.reduce((s, r) => s + (extractBsRowAmount(list, r) ?? 0), 0);
    const liabTop = liabilities.reduce((s, r) => s + (extractBsRowAmount(list, r) ?? 0), 0);

    expect(assetTotal - assetTop, "기타자산").toBeGreaterThanOrEqual(0);
    expect(liabTotal - liabTop, "기타부채").toBeGreaterThanOrEqual(0);
  });
}
