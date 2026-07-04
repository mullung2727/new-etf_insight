import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

const API = "http://localhost:8000";

const reports = [
  {
    researchId: "11",
    brokerName: "대신증권",
    title: "실적 상향",
    writeDate: "2026-07-03",
    downloaded: false,
  },
];

async function mockResearch(page: Page) {
  await page.route(`${API}/research/search*`, (route) =>
    route.fulfill({
      json: [
        { code: "005930", name: "삼성전자" },
        { code: "000660", name: "SK하이닉스" },
      ],
    })
  );
  await page.route(`${API}/research/stock/005930/reports*`, (route) =>
    route.fulfill({
      json: {
        code: "005930",
        name: "삼성전자",
        total: reports.length,
        already: 0,
        reports,
      },
    })
  );
}

test("autocomplete uses theme tokens and emphasizes name over code", async ({ page }) => {
  await mockResearch(page);
  await page.goto("/research");

  await page.getByPlaceholder("예: 삼성전자 또는 005930").fill("삼성");

  const list = page.locator("ul").filter({ hasText: "삼성전자" });
  await expect(list).toHaveClass(/bg-popover/);
  await expect(list).toHaveClass(/text-popover-foreground/);
  await expect(list.getByText("005930")).toHaveClass(/text-muted-foreground/);
});

test("selected stock renders as badge and clear resets report state", async ({ page }) => {
  await mockResearch(page);
  await page.goto("/research");

  await page.getByPlaceholder("예: 삼성전자 또는 005930").fill("삼성");
  await page.getByText("삼성전자", { exact: false }).click();

  await expect(page.getByText("삼성전자", { exact: true })).toBeVisible();
  await expect(page.getByText("005930", { exact: true })).toBeVisible();
  await expect(page.getByPlaceholder("예: 삼성전자 또는 005930")).toHaveCount(0);

  await page.getByRole("button", { name: "조회" }).click();
  await expect(page.getByText(/총 1건/)).toBeVisible();

  await page.getByRole("button", { name: "선택 해제" }).click();

  await expect(page.getByPlaceholder("예: 삼성전자 또는 005930")).toBeVisible();
  await expect(page.getByText(/총 1건/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /다운로드/ })).toBeDisabled();
});

test("date range defaults to today minus three months and persists on reload", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-07-04T12:00:00+09:00"));
  await mockResearch(page);
  await page.goto("/research");

  await expect(page.getByLabel("시작일")).toHaveValue("2026-04-04");
  await expect(page.getByLabel("종료일")).toHaveValue("2026-07-04");

  await page.getByLabel("시작일").fill("2026-05-01");
  await page.getByLabel("종료일").fill("2026-06-30");
  await page.reload();

  await expect(page.getByLabel("시작일")).toHaveValue("2026-05-01");
  await expect(page.getByLabel("종료일")).toHaveValue("2026-06-30");
});
