import { test, expect } from "@playwright/test";

type CompareRow = { key: string; label: string; type: string; values: (number | null)[] };
type CompareBody = {
  corpName: string;
  fsDiv: "CFS" | "OFS";
  periods: number[];
  rows: CompareRow[];
};

// 두 종목 모두 beforeAll에서 한 번만 호출 (DART 호출 최소화)
let samsung: CompareBody;
let m83: CompareBody;

test.beforeAll(async ({ request }) => {
  const [sRes, mRes] = await Promise.all([
    request.get("/api/financial/compare?corp_code=00126380&years=5"),
    request.get("/api/financial/compare?corp_code=01704680&years=5"),
  ]);
  expect(sRes.status()).toBe(200);
  expect(mRes.status()).toBe(200);
  samsung = await sRes.json();
  m83 = await mRes.json();
});

// ── 응답 구조 ──────────────────────────────────────────────────────────────
test("삼성전자 응답 구조", () => {
  expect(samsung.corpName).toBeTruthy();
  expect(samsung.fsDiv).toBe("CFS");
  expect(samsung.periods).toHaveLength(5);
  // 금액 6 + 비율 4 = 10행
  expect(samsung.rows).toHaveLength(10);
});

test("M83 응답 구조", () => {
  expect(m83.corpName).toBeTruthy();
  expect(["CFS", "OFS"]).toContain(m83.fsDiv);
  expect(m83.periods).toHaveLength(5);
  expect(m83.rows).toHaveLength(10);
});

// ── 금액 행 ────────────────────────────────────────────────────────────────
test("삼성전자 금액 6행 레이블·타입", () => {
  const amountKeys = ["revenue", "opProfit", "netIncome", "totalAssets", "totalLiab", "totalEquity"];
  for (const key of amountKeys) {
    const row = samsung.rows.find(r => r.key === key);
    expect(row, `${key} 행 없음`).toBeDefined();
    expect(row!.type).toBe("amount");
  }
});

test("삼성전자 매출액 — 5년 전부 값 존재", () => {
  const row = samsung.rows.find(r => r.key === "revenue")!;
  expect(row.values.every(v => v !== null)).toBe(true);
  // 2024 매출액은 양수
  const idx2024 = samsung.periods.indexOf(2024);
  if (idx2024 >= 0) expect(row.values[idx2024]).toBeGreaterThan(0);
});

// ── 비율 행 ────────────────────────────────────────────────────────────────
test("삼성전자 비율 4행 레이블·타입", () => {
  const ratioKeys = ["opMargin", "roe", "debtRatio", "revenueGrowth"];
  for (const key of ratioKeys) {
    const row = samsung.rows.find(r => r.key === key);
    expect(row, `${key} 행 없음`).toBeDefined();
    expect(row!.type).toBe("ratio");
  }
});

test("삼성전자 비율 — 2023 이전 null, 2024 이상 값 존재", () => {
  const roeRow = samsung.rows.find(r => r.key === "roe")!;
  for (const year of [2021, 2022]) {
    const idx = samsung.periods.indexOf(year);
    if (idx >= 0) expect(roeRow.values[idx], `ROE ${year}`).toBeNull();
  }
  for (const year of [2024, 2025]) {
    const idx = samsung.periods.indexOf(year);
    if (idx >= 0) expect(roeRow.values[idx], `ROE ${year}`).not.toBeNull();
  }
});

test("삼성전자 영업이익률 — 금액 계산, 2024 양수", () => {
  const row = samsung.rows.find(r => r.key === "opMargin")!;
  const idx2024 = samsung.periods.indexOf(2024);
  if (idx2024 >= 0) {
    expect(row.values[idx2024]).not.toBeNull();
    expect(row.values[idx2024]).toBeGreaterThan(0);
  }
});

// ── M83 — 상장 전 연도 null ────────────────────────────────────────────────
test("M83 2021~2023 금액 행 null", () => {
  const revenueRow = m83.rows.find(r => r.key === "revenue")!;
  for (const year of [2021, 2022, 2023]) {
    const idx = m83.periods.indexOf(year);
    if (idx >= 0) expect(revenueRow.values[idx], `revenue ${year}`).toBeNull();
  }
});

test("M83 2024~2025 금액 행 값 존재", () => {
  const revenueRow = m83.rows.find(r => r.key === "revenue")!;
  for (const year of [2024, 2025]) {
    const idx = m83.periods.indexOf(year);
    if (idx >= 0) expect(revenueRow.values[idx], `revenue ${year}`).not.toBeNull();
  }
});

// ── 유효성 검사 ────────────────────────────────────────────────────────────
test("corp_code 없으면 400", async ({ request }) => {
  const res = await request.get("/api/financial/compare?years=5");
  expect(res.status()).toBe(400);
});
