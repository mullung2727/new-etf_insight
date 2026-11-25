import { test, expect } from "@playwright/test";
import { twr, twrIndex, twrMdd } from "../lib/performance-db";

test("입출금 없음: 일별 수익률·누적지수·MDD", () => {
  const rows = [
    { equity: 100, flow: 100 },
    { equity: 110, flow: 0 },
    { equity: 99, flow: 0 },
  ];
  const r = twr(rows);
  expect(r[0]).toBeCloseTo(0, 10);
  expect(r[1]).toBeCloseTo(0.1, 10);
  expect(r[2]).toBeCloseTo(-0.1, 10);
  const idx = twrIndex(rows);
  expect(idx[idx.length - 1]).toBeCloseTo(0.99, 10);
  expect(twrMdd(rows)).toBeCloseTo(-0.1, 10);
});

test("입금이 수익으로 안 잡힘", () => {
  const rows = [
    { equity: 100, flow: 100 },
    { equity: 150, flow: 50 },
  ];
  expect(twr(rows)[1]).toBeCloseTo(0, 10);
});

test("출금이 낙폭으로 안 잡힘", () => {
  const rows = [
    { equity: 100, flow: 100 },
    { equity: 60, flow: -40 },
  ];
  expect(twr(rows)[1]).toBeCloseTo(0, 10);
  expect(twrMdd(rows)).toBeCloseTo(0, 10);
});
