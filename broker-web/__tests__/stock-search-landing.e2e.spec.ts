import { test, expect } from "@playwright/test";

// /stock 검색 랜딩 e2e — corps 인덱스 mock. 선택 시 stock_code로 허브 진입.
const CORPS = [
  { corp_code: "00126380", corp_name: "삼성전자", stock_code: "005930" },
  { corp_code: "00164779", corp_name: "SK하이닉스", stock_code: "000660" },
];

test("검색·선택 → /stock/{stock_code}?tab=financial 로 이동", async ({ page }) => {
  await page.route("**/api/corps", (r) => r.fulfill({ json: CORPS }));

  await page.goto("/stock");
  await expect(page.getByTestId("corps-ready")).toBeVisible();

  await page.getByTestId("corp-search-input").fill("삼성전자");
  await page.getByTestId("autocomplete-item").first().click();

  await expect(page).toHaveURL(/\/stock\/005930\?.*tab=financial/);
});

test("변형: 랜딩엔 재무 전용 문구 없음", async ({ page }) => {
  await page.route("**/api/corps", (r) => r.fulfill({ json: CORPS }));
  await page.goto("/stock");
  await expect(page.getByTestId("corps-ready")).toBeVisible();
  await expect(page.getByText("DART Financial Compare")).toHaveCount(0);
});
