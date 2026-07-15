import { test, expect } from "@playwright/test";

// 레거시 라우트 유지 + 리다이렉트 — 북마크·외부 링크 무손상.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/corps", (r) => r.fulfill({ json: [] }));
});

test("/financial → /stock 리다이렉트", async ({ page }) => {
  await page.goto("/financial");
  await expect(page).toHaveURL(/\/stock$/);
});

test("/research → /stock 리다이렉트", async ({ page }) => {
  await page.goto("/research");
  await expect(page).toHaveURL(/\/stock$/);
});
