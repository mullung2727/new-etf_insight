import { test, expect } from "@playwright/test";

type Row = { key: string; type: string; section: string; values: (number | null)[] };
type Body = { corpName: string; fsDiv: string; periods: string[]; rows: Row[] };

let samsung: Body;
let vaiv: Body;

test.beforeAll(async ({ request }) => {
  const [s, v] = await Promise.all([
    request.get("/api/financial/compare?corp_code=00126380&mode=quarterly&count=8"), // 삼성전자
    request.get("/api/financial/compare?corp_code=00403793&mode=quarterly&count=8"), // 바이브컴퍼니
  ]);
  expect(s.status()).toBe(200);
  expect(v.status()).toBe(200);
  samsung = await s.json();
  vaiv = await v.json();
});

test("periods — 8분기, YYYYQn 포맷, 오름차순", () => {
  expect(samsung.periods).toHaveLength(8);
  for (const p of samsung.periods) expect(p).toMatch(/^\d{4}Q[1-4]$/);
  const sorted = [...samsung.periods].sort();
  expect(samsung.periods).toEqual(sorted); // 문자열 정렬 = 시간순
});

test("Q1~Q4 라벨 다양성 (2년치면 4분기 다 등장)", () => {
  const quarters = new Set(samsung.periods.map(p => p.slice(-2)));
  expect(quarters).toEqual(new Set(["Q1", "Q2", "Q3", "Q4"]));
});

test("행 구조 — 연간과 동일 24행 (bs17+is3+ratio4)", () => {
  expect(samsung.rows).toHaveLength(24);
});

test("삼성 매출 — 8분기 전부 값 존재 + 단독분기(연간 아님)", () => {
  const rev = samsung.rows.find(r => r.key === "revenue")!;
  expect(rev.values.every(v => v !== null && v > 0)).toBe(true);
  // 단독분기 매출은 60~100조 → 연간 누적(300조)이 흘러들면 실패
  expect(Math.max(...(rev.values as number[]))).toBeLessThan(150e12);
});

test("삼성 Q4 파생 — 값 존재 + 양수", () => {
  const rev = samsung.rows.find(r => r.key === "revenue")!;
  const q4Idx = samsung.periods
    .map((p, i) => (p.endsWith("Q4") ? i : -1))
    .filter(i => i >= 0);
  expect(q4Idx.length).toBeGreaterThan(0);
  for (const i of q4Idx) {
    expect(rev.values[i], `Q4 ${samsung.periods[i]} 매출`).not.toBeNull();
    expect(rev.values[i]!).toBeGreaterThan(0);
  }
});

test("삼성 자산총계(BS 시점) — 8분기 전부 값 존재", () => {
  const assets = samsung.rows.find(r => r.key === "totalAssets")!;
  expect(assets.values.every(v => v !== null && v > 0)).toBe(true);
});

test("삼성 비율 — 최근분기 ROE 값 존재 (분기 Indx 지원)", () => {
  const roe = samsung.rows.find(r => r.key === "roe")!;
  expect(roe.values[roe.values.length - 1]).not.toBeNull();
});

test("바이브 — 8분기 반환 + 최근 매출 값 존재", () => {
  expect(vaiv.periods).toHaveLength(8);
  const rev = vaiv.rows.find(r => r.key === "revenue")!;
  expect(rev.values[rev.values.length - 1]).not.toBeNull();
});
