import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { ACTIVE_PATH, DELETED_PATH } from "../lib/telegram-channels";

// 파일 변경하므로 스냅샷 복원
let activeBefore: string;
let deletedExisted = false;
let deletedBefore = "";

test.beforeAll(async () => {
  activeBefore = await fs.readFile(ACTIVE_PATH(), "utf-8");
  try {
    deletedBefore = await fs.readFile(DELETED_PATH(), "utf-8");
    deletedExisted = true;
  } catch {
    deletedExisted = false;
  }
});
test.afterAll(async () => {
  await fs.writeFile(ACTIVE_PATH(), activeBefore, "utf-8");
  if (deletedExisted) await fs.writeFile(DELETED_PATH(), deletedBefore, "utf-8");
  else await fs.rm(DELETED_PATH(), { force: true });
});

test("표에 채널·URL·종목탐색 체크박스 렌더", async ({ page }) => {
  await page.goto("/admin/telegram");
  await expect(page.getByRole("heading", { name: "텔레그램 채널 관리" })).toBeVisible();
  // getfeed 행 URL 링크
  await expect(page.getByRole("link", { name: /t\.me\/s\/getfeed/ })).toBeVisible();
  // discovery 채널 체크됨
  const getfeedRow = page.locator("tr", { hasText: "t.me/s/getfeed" });
  await expect(getfeedRow.getByRole("checkbox")).toBeChecked();
  // 비-discovery 채널 미체크
  const companyRow = page.locator("tr", { hasText: "t.me/s/companyreport" });
  await expect(companyRow.getByRole("checkbox")).not.toBeChecked();
});

test("단일 저장 버튼: 편집 시 활성화, 저장 후 유지", async ({ page }) => {
  await page.goto("/admin/telegram");
  const saveBtn = page.getByRole("button", { name: "저장" });
  await expect(saveBtn).toBeDisabled(); // 변경 없으면 비활성

  // 실채널 오염 방지 — throwaway 채널 추가 후 그걸로 토글/저장
  await page.getByPlaceholder(/채널 URL 붙여넣기/).fill("https://t.me/s/e2e_save_zzz");
  await page.getByRole("button", { name: "+ 채널 추가" }).click();
  const row = page.locator("tr", { hasText: "e2e_save_zzz" });
  await expect(row).toBeVisible();
  await expect(saveBtn).toBeDisabled(); // 추가는 즉시반영이라 dirty 아님

  await row.getByRole("checkbox").click(); // discovery 켜기(base-ui = 버튼형)
  await expect(saveBtn).toBeEnabled();
  await saveBtn.click();

  await page.reload();
  await expect(page.locator("tr", { hasText: "e2e_save_zzz" }).getByRole("checkbox")).toBeChecked();
});

test("추가 → 삭제(삭제섹션 이동) → 복구 전체 흐름", async ({ page }) => {
  await page.goto("/admin/telegram");

  // 추가
  await page.getByPlaceholder(/채널 URL 붙여넣기/).fill("https://t.me/s/e2e_ui_channel_zzz");
  await page.getByPlaceholder("이름(선택)").fill("UI테스트");
  await page.getByRole("button", { name: "+ 채널 추가" }).click();
  const newRow = page.locator("tr", { hasText: "e2e_ui_channel_zzz" });
  await expect(newRow).toBeVisible();

  // 삭제 → 삭제섹션
  await newRow.getByRole("button", { name: "삭제" }).click();
  await expect(page.locator("tr", { hasText: "e2e_ui_channel_zzz" })).toHaveCount(0);
  await page.getByRole("button", { name: /삭제된 채널/ }).click();
  const delRow = page.locator("div", { hasText: "e2e_ui_channel_zzz" }).last();
  await expect(delRow).toBeVisible();

  // 복구
  await delRow.getByRole("button", { name: "복구" }).click();
  await expect(page.locator("tr", { hasText: "e2e_ui_channel_zzz" })).toBeVisible();
});
