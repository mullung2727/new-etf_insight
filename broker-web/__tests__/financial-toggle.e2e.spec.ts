import { test, expect } from "@playwright/test";

// 재무 탭 연간⇄분기 토글 e2e — 허브 /stock/[code]?tab=financial.
// URL ?fin_mode= 파라미터 서버 재렌더 방식. 실 DART 데이터 없이 토글 메커니즘만 검증.
// (연/분기 데이터 shape는 api-compare*.spec 이 커버)
test("재무 탭 연간⇄분기 토글이 URL fin_mode + 활성상태를 갱신", async ({ page }) => {
  await page.goto("/stock/005930?tab=financial");

  const annual = page.getByTestId("mode-annual");
  const quarterly = page.getByTestId("mode-quarterly");

  // 기본 = 연간 활성
  await expect(annual).toHaveAttribute("aria-pressed", "true");
  await expect(quarterly).toHaveAttribute("aria-pressed", "false");

  // 분기 클릭 → URL fin_mode=quarterly, 분기 활성
  await quarterly.click();
  await expect(page).toHaveURL(/fin_mode=quarterly/);
  await expect(quarterly).toHaveAttribute("aria-pressed", "true");
  await expect(annual).toHaveAttribute("aria-pressed", "false");

  // 다시 연간 → fin_mode=annual, 연간 활성
  await annual.click();
  await expect(page).toHaveURL(/fin_mode=annual/);
  await expect(annual).toHaveAttribute("aria-pressed", "true");
});
