import { test, expect } from "@playwright/test";

// 전역 nav 링크 — 종목 분석 단일 진입, 재무제표·리포트 nav 제거.
test("nav: 종목 분석 있고 재무제표·리포트 없음", async ({ page }) => {
  await page.route("**/api/corps", (r) => r.fulfill({ json: [] }));
  await page.goto("/stock");

  const nav = page.locator("header nav");
  await expect(nav.getByRole("link", { name: "종목 분석" })).toHaveAttribute("href", "/stock");
  await expect(nav.getByRole("link", { name: "재무제표" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "리포트" })).toHaveCount(0);
});
