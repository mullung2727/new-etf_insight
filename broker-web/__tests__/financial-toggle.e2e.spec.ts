import { test, expect } from "@playwright/test";

// 연간⇄분기 토글 e2e — 삼성전자(005930) 검색 후 모드 전환 검증
test("연간→분기 토글 시 기간 헤더가 분기 라벨로 갱신", async ({ page }) => {
  await page.goto("/financial");

  // 기업 인덱스 로딩 대기 후 종목코드로 검색
  await page.getByTestId("corps-ready").waitFor({ timeout: 60_000 });
  await page.getByTestId("corp-search-input").fill("005930");
  await page.getByTestId("autocomplete-item").first().click();

  // 연간 결과 — 기간 5개, Q 없음
  const yearCells = page.locator('[data-testid="compare-table"] thead th[data-year]');
  await expect(yearCells).toHaveCount(5, { timeout: 60_000 });
  for (const t of await yearCells.allInnerTexts()) expect(t).not.toContain("Q");
  await expect(page.getByTestId("compare-header")).toContainText("연간");

  // 분기 토글 → 기간 8개, 전부 YYYYQn
  await page.getByTestId("mode-quarterly").click();
  await expect(yearCells).toHaveCount(8, { timeout: 60_000 });
  for (const t of await yearCells.allInnerTexts()) expect(t).toMatch(/\d{4}Q[1-4]/);
  await expect(page.getByTestId("compare-header")).toContainText("분기");

  // 다시 연간 → 5개 복귀
  await page.getByTestId("mode-annual").click();
  await expect(yearCells).toHaveCount(5, { timeout: 60_000 });
});
