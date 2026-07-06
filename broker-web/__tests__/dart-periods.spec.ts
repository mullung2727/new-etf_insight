import { test, expect } from "@playwright/test";
import { buildPeriods, QUARTER_REPRT } from "../lib/dart-periods";

test("annual: 5년 오름차순, 전부 11011", () => {
  const p = buildPeriods("annual", 5, { year: 2024 });
  expect(p.map(x => x.label)).toEqual(["2020", "2021", "2022", "2023", "2024"]);
  expect(p.every(x => x.reprtCode === "11011")).toBe(true);
  expect(p.every(x => x.quarter === undefined)).toBe(true);
});

test("quarterly: 8분기 연도경계 넘어 역산 후 오름차순", () => {
  const p = buildPeriods("quarterly", 8, { year: 2024, quarter: 4 });
  expect(p.map(x => x.label)).toEqual([
    "2023Q1", "2023Q2", "2023Q3", "2023Q4",
    "2024Q1", "2024Q2", "2024Q3", "2024Q4",
  ]);
});

test("quarterly: reprt_code 매핑 Q1=13 Q2=12 Q3=14 Q4=11", () => {
  const p = buildPeriods("quarterly", 4, { year: 2024, quarter: 4 });
  expect(p.map(x => x.reprtCode)).toEqual(["11013", "11012", "11014", "11011"]);
  expect(QUARTER_REPRT).toEqual({ 1: "11013", 2: "11012", 3: "11014", 4: "11011" });
});

test("quarterly: 분기 경계 시작(Q2)도 정확히 역산", () => {
  const p = buildPeriods("quarterly", 3, { year: 2024, quarter: 2 });
  expect(p.map(x => x.label)).toEqual(["2023Q4", "2024Q1", "2024Q2"]);
});
