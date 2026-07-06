import { test, expect } from "@playwright/test";

test("파일 목록·경로·설명 표시 + 선택 시 원문 로드", async ({ page }) => {
  await page.goto("/admin/json");

  // 3개 파일 라벨 노출
  await expect(page.getByText("보유종목 별칭")).toBeVisible();
  await expect(page.getByText("텔레그램 채널")).toBeVisible();
  await expect(page.getByText("배치 레지스트리")).toBeVisible();

  // 경로가 목록에 표시됨
  await expect(page.getByText("etl/scripts/telegram_channels.json").first()).toBeVisible();

  // 선택 → 경로·설명·원문 로드
  await page.getByRole("button", { name: /텔레그램 채널/ }).click();
  await expect(page.getByText(/채널 목록/)).toBeVisible(); // description
  const ta = page.locator("textarea");
  await expect(ta).toBeVisible();
  await expect(ta).toContainText("source_url");
});

test("잘못된 JSON → 오류 표시 + 저장 비활성", async ({ page }) => {
  await page.goto("/admin/json");
  await page.getByRole("button", { name: /텔레그램 채널/ }).click();
  const ta = page.locator("textarea");
  await ta.fill("{ 깨진 json");
  await expect(page.getByText(/JSON 오류/)).toBeVisible();
  await expect(page.getByRole("button", { name: "저장" })).toBeDisabled();
});

test("caution 파일은 경고 배지·문구 표시", async ({ page }) => {
  await page.goto("/admin/json");
  await page.getByRole("button", { name: /배치 레지스트리/ }).click();
  await expect(page.getByText(/배치가 실패할 수 있습니다/)).toBeVisible();
});
