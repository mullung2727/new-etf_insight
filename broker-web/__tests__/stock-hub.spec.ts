import { test, expect } from "@playwright/test";
import { resolveTab, stockHubHref, STOCK_TABS } from "@/lib/stock-hub";

test("resolveTab: valid tab passes through", () => {
  for (const t of STOCK_TABS) expect(resolveTab(t)).toBe(t);
});

test("resolveTab: unknown/empty → overview", () => {
  expect(resolveTab(undefined)).toBe("overview");
  expect(resolveTab(null)).toBe("overview");
  expect(resolveTab("")).toBe("overview");
  expect(resolveTab("bogus")).toBe("overview");
});

test("stockHubHref: overview tab omitted (default)", () => {
  expect(stockHubHref("005930")).toBe("/stock/005930");
  expect(stockHubHref("005930", { tab: "overview" })).toBe("/stock/005930");
});

test("stockHubHref: non-default tab included", () => {
  expect(stockHubHref("005930", { tab: "chart" })).toBe("/stock/005930?tab=chart");
});

test("STOCK_TABS: research 탭 포함", () => {
  expect(STOCK_TABS).toContain("research");
});

test("STOCK_TABS: mentions 통합, telegram/youtube 개별탭 제거", () => {
  expect(STOCK_TABS).toContain("mentions");
  expect(STOCK_TABS).not.toContain("telegram");
  expect(STOCK_TABS).not.toContain("youtube");
});

test("resolveTab: 레거시 telegram/youtube → mentions 호환", () => {
  expect(resolveTab("telegram")).toBe("mentions");
  expect(resolveTab("youtube")).toBe("mentions");
});

test("stockHubHref: research 탭", () => {
  expect(stockHubHref("005930", { tab: "research" })).toBe("/stock/005930?tab=research");
});

test("stockHubHref: preserves and encodes date/name", () => {
  const href = stockHubHref("005930", { tab: "chart", date: "20260707", name: "삼성전자" });
  expect(href).toContain("/stock/005930?");
  expect(href).toContain("tab=chart");
  expect(href).toContain("date=20260707");
  expect(href).toContain(`name=${encodeURIComponent("삼성전자")}`);
});
