import { test, expect } from "@playwright/test";

test.describe("재무제표 다기간 비교 페이지", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/financial");
    // /api/corps 로드 완료 대기 (대형 ZIP 파싱, 최대 60s)
    await page.waitForSelector("[data-testid='corps-ready']", { timeout: 60_000 });
  });

  // ── 초기 상태 ──────────────────────────────────────────────────────────────
  test("초기 상태 — SearchBar 표시, 표 없음", async ({ page }) => {
    await expect(page.locator("[data-testid='corp-search-input']")).toBeVisible();
    await expect(page.locator("[data-testid='compare-table']")).not.toBeVisible();
  });

  // ── 삼성전자 검색 → 비교 표 ─────────────────────────────────────────────────
  test("삼성전자 검색 → 비교 표 10행 표시", async ({ page }) => {
    await page.locator("[data-testid='corp-search-input']").fill("삼성전자");
    await page.waitForSelector("[data-testid='autocomplete-item']");
    await page.locator("[data-testid='autocomplete-item']").first().click();

    await page.waitForSelector("[data-testid='compare-table']", { timeout: 90_000 });

    // 연도 헤더 5개
    const yearCols = page.locator("[data-testid='compare-table'] [data-year]");
    await expect(yearCols).toHaveCount(5);

    // 금액 6행 + 비율 4행
    const rows = page.locator("[data-testid='compare-table'] [data-row-key]");
    await expect(rows).toHaveCount(10);
  });

  test("삼성전자 — 필수 행 레이블 표시", async ({ page }) => {
    await page.locator("[data-testid='corp-search-input']").fill("삼성전자");
    await page.waitForSelector("[data-testid='autocomplete-item']");
    await page.locator("[data-testid='autocomplete-item']").first().click();
    await page.waitForSelector("[data-testid='compare-table']", { timeout: 90_000 });

    for (const key of ["revenue", "opProfit", "netIncome", "totalAssets", "totalLiab", "totalEquity", "opMargin", "roe", "debtRatio", "revenueGrowth"]) {
      await expect(page.locator(`[data-testid='compare-table'] [data-row-key='${key}']`), `${key} 행`).toBeVisible();
    }
  });

  test("삼성전자 — 최신 연도 매출액 값 표시 (— 아님)", async ({ page }) => {
    await page.locator("[data-testid='corp-search-input']").fill("삼성전자");
    await page.waitForSelector("[data-testid='autocomplete-item']");
    await page.locator("[data-testid='autocomplete-item']").first().click();
    await page.waitForSelector("[data-testid='compare-table']", { timeout: 90_000 });

    // 마지막 값 셀 (최신 연도)
    const lastCell = page.locator("[data-row-key='revenue'] td").last();
    const text = await lastCell.textContent();
    expect(text?.trim()).not.toBe("—");
    expect(text?.trim()).not.toBe("");
  });

  test("삼성전자 — 오래된 연도 ROE null (— 표시)", async ({ page }) => {
    await page.locator("[data-testid='corp-search-input']").fill("삼성전자");
    await page.waitForSelector("[data-testid='autocomplete-item']");
    await page.locator("[data-testid='autocomplete-item']").first().click();
    await page.waitForSelector("[data-testid='compare-table']", { timeout: 90_000 });

    // nth(0)=label, nth(1)=첫 번째 연도 값 (2021) = — (비율 2023+ 이전)
    const firstValueCell = page.locator("[data-row-key='roe'] td").nth(1);
    await expect(firstValueCell).toHaveText("—");
  });

  // ── M83 — 상장 전 연도 — 표시 ───────────────────────────────────────────────
  test("M83 검색 → 2021~2023 매출액 — 표시", async ({ page }) => {
    await page.locator("[data-testid='corp-search-input']").fill("M83");
    await page.waitForSelector("[data-testid='autocomplete-item']");
    await page.locator("[data-testid='autocomplete-item']").first().click();
    await page.waitForSelector("[data-testid='compare-table']", { timeout: 90_000 });

    // 기간 헤더에서 2021 인덱스 확인
    const yearHeaders = page.locator("[data-testid='compare-table'] [data-year]");
    const years = await yearHeaders.allTextContents();
    const idx2021 = years.findIndex(y => y.trim() === "2021");

    if (idx2021 >= 0) {
      const cells = page.locator("[data-row-key='revenue'] td");
      // nth(0)=label, nth(1)=첫 연도 값 → idx2021+1
      await expect(cells.nth(idx2021 + 1)).toHaveText("—");
    }
  });

  // ── 타이틀 헤더 ─────────────────────────────────────────────────────────────
  test("삼성전자 — 표 헤더에 기업명·fs_div 표시", async ({ page }) => {
    await page.locator("[data-testid='corp-search-input']").fill("삼성전자");
    await page.waitForSelector("[data-testid='autocomplete-item']");
    await page.locator("[data-testid='autocomplete-item']").first().click();
    await page.waitForSelector("[data-testid='compare-table']", { timeout: 90_000 });

    const header = page.locator("[data-testid='compare-header']");
    await expect(header).toContainText("삼성전자");
  });
});
