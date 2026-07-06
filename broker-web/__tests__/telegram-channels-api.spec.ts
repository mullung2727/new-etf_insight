import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { ACTIVE_PATH, DELETED_PATH } from "../lib/telegram-channels";

// 실제 파일을 건드리므로 스냅샷 후 복원
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

const TEST_URL = "https://t.me/s/e2e_test_channel_zzz";
const KEY = "e2e_test_channel_zzz";

test.describe.serial("텔레그램 채널 CRUD", () => {
  test("GET → active/deleted 구조", async ({ request }) => {
    const res = await request.get("/api/telegram-channels");
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(Array.isArray(body.active)).toBe(true);
    expect(body.active.some((c: { key: string }) => c.key === "companyreport")).toBe(true);
  });

  test("추가(discovery on) → active에 등장", async ({ request }) => {
    const res = await request.post("/api/telegram-channels", {
      data: { action: "add", url: TEST_URL, label: "테스트채널", discovery: true },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    const c = body.active.find((c: { key: string }) => c.key === KEY);
    expect(c).toMatchObject({ key: KEY, label: "테스트채널", discovery: true });
  });

  test("중복 추가 → 400", async ({ request }) => {
    const res = await request.post("/api/telegram-channels", {
      data: { action: "add", url: TEST_URL, discovery: false },
    });
    expect(res.status()).toBe(400);
  });

  test("수정(discovery off, label 변경)", async ({ request }) => {
    const res = await request.put("/api/telegram-channels", {
      data: { key: KEY, label: "수정됨", discovery: false },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    const c = body.active.find((c: { key: string }) => c.key === KEY);
    expect(c).toMatchObject({ label: "수정됨", discovery: false });
  });

  test("소프트delete → active에서 빠지고 deleted로", async ({ request }) => {
    const res = await request.delete("/api/telegram-channels", { data: { key: KEY } });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.active.some((c: { key: string }) => c.key === KEY)).toBe(false);
    expect(body.deleted.some((c: { key: string }) => c.key === KEY)).toBe(true);
  });

  test("복구 → 다시 active로", async ({ request }) => {
    const res = await request.post("/api/telegram-channels", {
      data: { action: "restore", key: KEY },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.active.some((c: { key: string }) => c.key === KEY)).toBe(true);
    expect(body.deleted.some((c: { key: string }) => c.key === KEY)).toBe(false);
  });
});
