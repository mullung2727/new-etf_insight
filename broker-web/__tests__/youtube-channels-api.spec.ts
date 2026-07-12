import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { ACTIVE_PATH, DELETED_PATH } from "../lib/youtube-channels";

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

// UC + 22 (형식만 맞으면 실존 불필요). tech §3.7 B
const KEY = "UCe2etestChannelId_zzzz1";
const TEST_URL = `https://www.youtube.com/channel/${KEY}`;

test.describe.serial("유튜브 채널 CRUD", () => {
  test("B1 GET → active/deleted 구조", async ({ request }) => {
    const res = await request.get("/api/youtube-channels");
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(Array.isArray(body.active)).toBe(true);
    expect(Array.isArray(body.deleted)).toBe(true);
  });

  test("B2 추가(discovery on) → active에 등장", async ({ request }) => {
    const res = await request.post("/api/youtube-channels", {
      data: {
        action: "add",
        url: TEST_URL,
        label: "테스트유튜브",
        discovery: true,
      },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    const c = body.active.find((x: { key: string }) => x.key === KEY);
    expect(c).toMatchObject({
      key: KEY,
      label: "테스트유튜브",
      discovery: true,
    });
  });

  test("B3 중복 추가 → 400", async ({ request }) => {
    const res = await request.post("/api/youtube-channels", {
      data: { action: "add", url: TEST_URL, discovery: false },
    });
    expect(res.status()).toBe(400);
  });

  test("B4 수정(discovery off, label 변경)", async ({ request }) => {
    const res = await request.put("/api/youtube-channels", {
      data: { key: KEY, label: "수정됨", discovery: false },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    const c = body.active.find((x: { key: string }) => x.key === KEY);
    expect(c).toMatchObject({ label: "수정됨", discovery: false });
  });

  test("B5 소프트delete → active에서 빠지고 deleted로", async ({ request }) => {
    const res = await request.delete("/api/youtube-channels", {
      data: { key: KEY },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.active.some((x: { key: string }) => x.key === KEY)).toBe(false);
    expect(body.deleted.some((x: { key: string }) => x.key === KEY)).toBe(true);
  });

  test("B6 복구 → 다시 active로", async ({ request }) => {
    const res = await request.post("/api/youtube-channels", {
      data: { action: "restore", key: KEY },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.active.some((x: { key: string }) => x.key === KEY)).toBe(true);
    expect(body.deleted.some((x: { key: string }) => x.key === KEY)).toBe(false);
  });

  test("B7 watch/shorts 영상 URL 추가 → 400", async ({ request }) => {
    const watch = await request.post("/api/youtube-channels", {
      data: {
        action: "add",
        url: "https://www.youtube.com/watch?v=F-UgZE6QZiQ",
        discovery: false,
      },
    });
    expect(watch.status()).toBe(400);

    const shorts = await request.post("/api/youtube-channels", {
      data: {
        action: "add",
        url: "https://www.youtube.com/shorts/F-UgZE6QZiQ",
        discovery: false,
      },
    });
    expect(shorts.status()).toBe(400);
  });
});
