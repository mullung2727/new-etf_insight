import { test, expect } from "@playwright/test";

type CompareRow = {
  key: string;
  label: string;
  type: string;
  section: "bs" | "is" | "ratio";
  values: (number | null)[];
};
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
// BS 17행(자산 top5 + 그 외 + 총계, 부채 top3 + 그 외 + 총계,
//         자본금·이익잉여금·기타(자본)·비지배지분·자본총계)
// + IS 3행 + 비율 4행 = 24행
test("삼성전자 응답 구조", () => {
  expect(samsung.corpName).toBeTruthy();
  expect(samsung.fsDiv).toBe("CFS");
  expect(samsung.periods).toHaveLength(5);
  expect(samsung.rows).toHaveLength(24);
});

test("M83 응답 구조", () => {
  expect(m83.corpName).toBeTruthy();
  expect(["CFS", "OFS"]).toContain(m83.fsDiv);
  expect(m83.periods).toHaveLength(5);
  expect(m83.rows).toHaveLength(24);
});

test("rows 섹션 순서 — bs → is → ratio", () => {
  const sections = samsung.rows.map(r => r.section);
  expect(sections).toEqual([
    ...Array(17).fill("bs"), ...Array(3).fill("is"), ...Array(4).fill("ratio"),
  ]);
});

// ── BS 동적 top-N 행 ────────────────────────────────────────────────────────
test("삼성전자 — BS 동적 행 키·순서·레이블", () => {
  const bsRows = samsung.rows.filter(r => r.section === "bs");
  expect(bsRows.map(r => r.key)).toEqual([
    "bsAsset0", "bsAsset1", "bsAsset2", "bsAsset3", "bsAsset4", "bsAssetEtc", "totalAssets",
    "bsLiab0", "bsLiab1", "bsLiab2", "bsLiabEtc", "totalLiab",
    "equityCapital", "equityRetained", "equityEtc", "equityNci", "totalEquity",
  ]);
  expect(bsRows[0].label).toBe("유형자산");
  expect(bsRows[5].label).toBe("그 외 자산");
  expect(bsRows[7].label).toBe("미지급비용");
  expect(bsRows[12].label).toBe("자본금");
  expect(bsRows[14].label).toBe("기타(자본)");
});

// ── 자본 고정 행 (방안 2 — 표준 계정 전 종목 존재, fixture 6개사 확인) ────────
test("삼성전자 — 자본 구성 정확값 (2025)", () => {
  const idx = samsung.periods.indexOf(2025);
  expect(idx).toBeGreaterThanOrEqual(0);
  const get = (key: string) => samsung.rows.find(r => r.key === key)!.values[idx];
  expect(get("equityCapital")).toBe(897_514_000_000);
  expect(get("equityRetained")).toBe(402_135_600_000_000);
  expect(get("equityNci")).toBe(12_007_082_000_000);
  // 기타(자본) = 자본총계 − (자본금 + 이익잉여금 + 비지배지분)
  expect(get("equityEtc")).toBe(21_280_141_000_000);
});

test("신한지주 — 자본 행 2021/2022 백필", () => {
  const row = shinhan.rows.find(r => r.key === "equityRetained")!;
  for (const year of [2021, 2022]) {
    const idx = shinhan.periods.indexOf(year);
    if (idx < 0) continue;
    expect(row.values[idx], `신한지주 이익잉여금 ${year}`).not.toBeNull();
    expect(row.values[idx]!).toBeGreaterThan(0);
  }
});

test("삼성전자 — 그 외 자산 = 자산총계 − top5 합 (2025)", () => {
  const idx = samsung.periods.indexOf(2025);
  expect(idx).toBeGreaterThanOrEqual(0);
  const etc = samsung.rows.find(r => r.key === "bsAssetEtc")!;
  // 566,942,110M − (215,304,784 + 67,965,021 + 57,856,378 + 52,636,828 + 51,127,642)M
  expect(etc.values[idx]).toBe(122_051_457_000_000);
});

test("신한지주 — BS 동적 행 2021/2022 백필", () => {
  const row = shinhan.rows.find(r => r.key === "bsAsset0")!;
  expect(row.label).toBe("상각후원가측정대출채권");
  for (const year of [2021, 2022]) {
    const idx = shinhan.periods.indexOf(year);
    if (idx < 0) continue;
    expect(row.values[idx], `신한지주 bsAsset0 ${year}`).not.toBeNull();
    expect(row.values[idx]!).toBeGreaterThan(0);
  }
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

// ── M83 — 상장 전 연도 백필 ──────────────────────────────────────────────
// [WHY] 2022/2023은 직접 조회 시 status=013이지만 2024 보고서의
//       frmtrm/bfefrmtrm(전기/전전기)에 값이 존재 → 백필로 표시
test("M83 2021 금액 행 null (백필 커버 범위 밖)", () => {
  const revenueRow = m83.rows.find(r => r.key === "revenue")!;
  const idx = m83.periods.indexOf(2021);
  if (idx >= 0) expect(revenueRow.values[idx], "revenue 2021").toBeNull();
});

test("M83 2022~2023 금액 행 백필 값 존재", () => {
  const revenueRow = m83.rows.find(r => r.key === "revenue")!;
  for (const year of [2022, 2023]) {
    const idx = m83.periods.indexOf(year);
    if (idx < 0) continue;
    expect(revenueRow.values[idx], `revenue ${year}`).not.toBeNull();
    expect(revenueRow.values[idx]!).toBeGreaterThan(0);
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

// ── 금융업 — 영업이익 폴백 (ifrs-full_ProfitLossFromOperatingActivities) ──────
// [WHY] 금융지주사는 dart_OperatingIncomeLoss 대신 ifrs-full_ProfitLossFromOperatingActivities[CIS]
//       사용. 신한(2023/2024), KB(2024), 하나(2023/2024) 실호출로 확인 (2026-06-10)
let shinhan: CompareBody;
let kb: CompareBody;
let hana: CompareBody;

test.beforeAll(async ({ request }) => {
  const [sRes, kRes, hRes] = await Promise.all([
    request.get("/api/financial/compare?corp_code=00382199&years=5"), // 신한지주
    request.get("/api/financial/compare?corp_code=00688996&years=5"), // KB금융
    request.get("/api/financial/compare?corp_code=00547583&years=5"), // 하나금융지주
  ]);
  expect(sRes.status()).toBe(200);
  expect(kRes.status()).toBe(200);
  expect(hRes.status()).toBe(200);
  shinhan = await sRes.json();
  kb      = await kRes.json();
  hana    = await hRes.json();
});

test("신한지주 — 5개 기간 반환", () => {
  expect(shinhan.periods).toHaveLength(5);
});

test("신한지주 — 2023/2024 영업이익 양수", () => {
  const row = shinhan.rows.find(r => r.key === "opProfit")!;
  for (const year of [2023, 2024]) {
    const idx = shinhan.periods.indexOf(year);
    if (idx < 0) continue;
    expect(row.values[idx], `신한지주 opProfit ${year}`).not.toBeNull();
    expect(row.values[idx]!).toBeGreaterThan(0);
  }
});

// [WHY] 직접 조회 시 2021/2022는 status=013이지만 2023 보고서의
//       frmtrm(2022)/bfefrmtrm(2021)에 값 존재 → 백필. 실호출 확정값(2026-06-10):
test("신한지주 — 2021/2022 금액 백필 (전기/전전기 값)", () => {
  const expected: Record<string, Record<number, number>> = {
    opProfit:    { 2021: 5_952_096_000_000,   2022: 5_905_564_000_000 },
    netIncome:   { 2021: 4_112_628_000_000,   2022: 4_755_514_000_000 },
    totalAssets: { 2021: 648_152_185_000_000, 2022: 664_433_230_000_000 },
  };
  for (const [key, byYear] of Object.entries(expected)) {
    const row = shinhan.rows.find(r => r.key === key)!;
    for (const [year, val] of Object.entries(byYear)) {
      const idx = shinhan.periods.indexOf(Number(year));
      if (idx < 0) continue;
      expect(row.values[idx], `신한지주 ${key} ${year}`).toBe(val);
    }
  }
});

test("신한지주 — 2021/2022 부채·자본총계 백필 값 존재", () => {
  for (const key of ["totalLiab", "totalEquity"]) {
    const row = shinhan.rows.find(r => r.key === key)!;
    for (const year of [2021, 2022]) {
      const idx = shinhan.periods.indexOf(year);
      if (idx < 0) continue;
      expect(row.values[idx], `신한지주 ${key} ${year}`).not.toBeNull();
    }
  }
});

test("신한지주 — revenue 전 연도 null (금융업 단일 매출 계정 없음)", () => {
  const row = shinhan.rows.find(r => r.key === "revenue")!;
  expect(row.values.every(v => v === null)).toBe(true);
});

test("KB금융 — 2023/2024 영업이익 양수", () => {
  const row = kb.rows.find(r => r.key === "opProfit")!;
  for (const year of [2023, 2024]) {
    const idx = kb.periods.indexOf(year);
    if (idx < 0) continue;
    expect(row.values[idx], `KB금융 opProfit ${year}`).not.toBeNull();
    expect(row.values[idx]!).toBeGreaterThan(0);
  }
});

test("하나금융지주 — 2023/2024 영업이익 양수", () => {
  const row = hana.rows.find(r => r.key === "opProfit")!;
  for (const year of [2023, 2024]) {
    const idx = hana.periods.indexOf(year);
    if (idx < 0) continue;
    expect(row.values[idx], `하나금융지주 opProfit ${year}`).not.toBeNull();
    expect(row.values[idx]!).toBeGreaterThan(0);
  }
});
