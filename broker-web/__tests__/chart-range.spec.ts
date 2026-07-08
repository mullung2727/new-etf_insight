import { test, expect } from "@playwright/test";
import { resolveRange, sliceByRange, CHART_RANGES } from "@/lib/chart-range";

test("resolveRange: valid passes through", () => {
  for (const r of CHART_RANGES) expect(resolveRange(r)).toBe(r);
});

test("resolveRange: unknown/empty → 3M default", () => {
  expect(resolveRange(undefined)).toBe("3M");
  expect(resolveRange(null)).toBe("3M");
  expect(resolveRange("bogus")).toBe("3M");
});

test("sliceByRange: keeps last N trading days per range", () => {
  const days = Array.from({ length: 300 }, (_, i) => i);
  expect(sliceByRange(days, "1M")).toHaveLength(22);
  expect(sliceByRange(days, "3M")).toHaveLength(66);
  expect(sliceByRange(days, "6M")).toHaveLength(126);
  expect(sliceByRange(days, "1Y")).toHaveLength(252);
  // 마지막 N개(최신) 유지
  expect(sliceByRange(days, "1M")[21]).toBe(299);
});

test("sliceByRange: shorter than N returns all", () => {
  const days = [1, 2, 3];
  expect(sliceByRange(days, "1Y")).toEqual([1, 2, 3]);
});
