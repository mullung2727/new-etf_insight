import { test, expect } from "@playwright/test";
import { twr, twrIndex, twrMdd, compareWindow, overlaySeries } from "../lib/performance-db";

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

test("compareWindow: 같은 구간 수익률과 차이 %p", () => {
  const live = [
    { date: "1", equity: 100, flow: 100 },
    { date: "2", equity: 110, flow: 0 },
    { date: "3", equity: 121, flow: 0 },
  ];
  const bt = [
    { date: "2", equity: 110, flow: 110 },
    { date: "3", equity: 115.5, flow: 0 },
  ];
  const r = compareWindow(live, bt);
  expect(r?.liveRet).toBeCloseTo(0.21, 10);
  expect(r?.btRet).toBeCloseTo(0.05, 10);
  expect(r?.diffPp).toBeCloseTo(16, 10);
});

test("compareWindow: bt 없으면 null", () => {
  const live = [{ date: "1", equity: 100, flow: 100 }];
  expect(compareWindow(live, [])).toBeNull();
});

test("overlaySeries: bt 시작 전날 값에 붙여 겹침", () => {
  const live = [
    { date: "1", equity: 100, flow: 100 },
    { date: "2", equity: 110, flow: 0 },
    { date: "3", equity: 121, flow: 0 },
  ];
  const bt = [
    { date: "2", equity: 110, flow: 110 },
    { date: "3", equity: 115.5, flow: 0 },
  ];
  const s = overlaySeries(live, bt);
  expect(s).toHaveLength(3);
  expect(s[0].livePct).toBeCloseTo(0, 10);
  expect(s[0].btPct).toBeNull();
  expect(s[1].livePct).toBeCloseTo(10, 10);
  expect(s[1].btPct).toBeCloseTo(0, 10);
  expect(s[2].livePct).toBeCloseTo(21, 10);
  expect(s[2].btPct).toBeCloseTo(5, 10);
});
