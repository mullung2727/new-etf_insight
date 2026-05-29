import { test, expect } from "@playwright/test";

test("list page loads and shows ETF rows", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("h1")).toContainText("신규 상장예정 ETF");
  const rows = page.locator("tbody tr");
  await expect(rows.first()).toBeVisible();
});

test("list page shows table headers", async ({ page }) => {
  await page.goto("/");
  for (const header of ["펀드명", "운용사", "지수명", "국가", "테마", "최초 공시일", "구성종목"]) {
    await expect(page.getByRole("columnheader", { name: header })).toBeVisible();
  }
});

test("date filter changes URL and updates list", async ({ page }) => {
  await page.goto("/");
  await page.locator('input[type="date"]').first().fill("2026-01-01");
  await page.getByRole("button", { name: "조회" }).click();
  await expect(page).toHaveURL(/begin=20260101/);
  await expect(page.locator("h1")).toBeVisible();
});

test("country filter updates URL", async ({ page }) => {
  await page.goto("/");
  const select = page.locator('[data-slot="select-trigger"]').first();
  await select.click();
  const options = page.locator('[data-slot="select-item"]');
  const count = await options.count();
  if (count > 1) {
    await options.nth(1).click();
    await page.getByRole("button", { name: "조회" }).click();
    await expect(page).toHaveURL(/country=/);
  }
});

test("row click navigates to detail page", async ({ page }) => {
  await page.goto("/");
  const firstRow = page.locator("tbody tr").first();
  await firstRow.click();
  await expect(page).toHaveURL(/\/etfs\//);
});

test("empty result shows no-data message", async ({ page }) => {
  await page.goto("/?begin=20000101&end=20000102");
  await expect(page.getByText("조건에 맞는 ETF가 없습니다.")).toBeVisible();
});

test("total count displayed", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/총 \d+건/)).toBeVisible();
});
