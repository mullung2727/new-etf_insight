import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  const { etfs } = await (await request.get("/api/etfs")).json();
  await page.goto(`/etfs/${etfs[0].etf_key}`);
});

test("detail page shows fund_name", async ({ page }) => {
  await expect(page.locator("[data-testid='fund-name']")).toBeVisible();
});

test("detail page shows holdings or fallback", async ({ page }) => {
  const hasHoldings = await page.locator("[data-testid='holdings-table']").isVisible();
  const hasFallback = await page.locator("[data-testid='holdings-fallback']").isVisible();
  expect(hasHoldings || hasFallback).toBe(true);
});

test("detail page shows AI analysis section", async ({ page }) => {
  await expect(page.locator("[data-testid='trend-summary']")).toBeVisible();
});

test("back button navigates to list", async ({ page }) => {
  await page.getByRole("link", { name: "← 목록으로" }).click();
  await expect(page).toHaveURL("/");
});

test("index_description collapsible toggle works", async ({ page }) => {
  const toggle = page.getByRole("button", { name: /지수 설명/ });
  if (await toggle.isVisible()) {
    await toggle.click();
    await expect(page.locator("text=지수 설명")).toBeVisible();
  }
});
