import { test, expect } from "@playwright/test";

test("stats page without keys shows prompt", async ({ page }) => {
  await page.goto("/stats");
  await expect(page.getByText("목록에서 ETF를 선택한 후")).toBeVisible();
  await expect(page.getByRole("link", { name: "← 목록으로" })).toBeVisible();
});

test("stats page with keys shows summary cards", async ({ page, request }) => {
  const { etfs } = await (await request.get("/api/etfs")).json();
  const withHoldings = etfs.filter((e: { has_holdings: boolean }) => e.has_holdings);
  if (withHoldings.length === 0) return;

  const keys = withHoldings.slice(0, 2).map((e: { etf_key: string }) => e.etf_key).join(",");
  await page.goto(`/stats?keys=${keys}`);

  await expect(page.locator("[data-testid='etf-count']")).toBeVisible();
  await expect(page.locator("[data-testid='holdings-etf-count']")).toBeVisible();
});

test("stats page with keys shows chart", async ({ page, request }) => {
  const { etfs } = await (await request.get("/api/etfs")).json();
  const withHoldings = etfs.filter((e: { has_holdings: boolean }) => e.has_holdings);
  if (withHoldings.length === 0) return;

  const keys = withHoldings.slice(0, 3).map((e: { etf_key: string }) => e.etf_key).join(",");
  await page.goto(`/stats?keys=${keys}`);

  await expect(page.locator("[data-testid='holdings-chart']")).toBeVisible();
});

test("stats back button returns to list", async ({ page }) => {
  await page.goto("/stats");
  await page.getByRole("link", { name: "← 목록으로" }).click();
  await expect(page).toHaveURL("/");
});
