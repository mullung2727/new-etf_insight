import { test, expect } from "@playwright/test";
import { initialRange, monthsAgoISO, todayISO } from "../lib/research-prefs";

test("initialRange returns today and three months ago by default", () => {
  const range = initialRange(new Date(2026, 6, 4), null);

  expect(range).toEqual({ since: "2026-04-04", until: "2026-07-04" });
});

test("initialRange crosses year boundary", () => {
  const range = initialRange(new Date(2026, 0, 15), null);

  expect(range).toEqual({ since: "2025-10-15", until: "2026-01-15" });
});

test("initialRange prefers saved values", () => {
  const range = initialRange(new Date(2026, 6, 4), {
    since: "2026-01-01",
    until: "2026-02-01",
  });

  expect(range).toEqual({ since: "2026-01-01", until: "2026-02-01" });
});

test("monthsAgoISO clamps missing month-end dates", () => {
  expect(monthsAgoISO(new Date(2026, 4, 31), 3)).toBe("2026-02-28");
});

test("todayISO formats local date without UTC conversion", () => {
  expect(todayISO(new Date(2026, 6, 4, 0, 30))).toBe("2026-07-04");
});
