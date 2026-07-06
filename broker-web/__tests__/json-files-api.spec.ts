import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { resolveManaged } from "../lib/json-admin";

test("GET (no key) → 관리 파일 목록(메타만, content 없음)", async ({ request }) => {
  const res = await request.get("/api/json-files");
  expect(res.status()).toBe(200);
  const list = await res.json();
  expect(list.map((f: { key: string }) => f.key)).toContain("cron_registry");
  expect(list[0]).toHaveProperty("description");
  expect(list[0]).not.toHaveProperty("content");
});

test("GET ?key= → 원문 + 메타", async ({ request }) => {
  const res = await request.get("/api/json-files?key=telegram_channels");
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.risk).toBe("safe");
  expect(body.relPath).toContain("telegram_channels.json");
  JSON.parse(body.content); // 파싱 가능해야
});

test("GET 미등록 key → 400", async ({ request }) => {
  const res = await request.get("/api/json-files?key=evil");
  expect(res.status()).toBe(400);
});

test("잘못된 JSON PUT → 400, 파일 불변", async ({ request }) => {
  const { absPath } = resolveManaged("telegram_channels");
  const before = await fs.readFile(absPath, "utf-8");
  const res = await request.put("/api/json-files", {
    data: { key: "telegram_channels", content: "{ not json" },
  });
  expect(res.status()).toBe(400);
  const after = await fs.readFile(absPath, "utf-8");
  expect(after).toBe(before);
});

test("미등록 key PUT → 400", async ({ request }) => {
  const res = await request.put("/api/json-files", {
    data: { key: "evil", content: "{}" },
  });
  expect(res.status()).toBe(400);
});

test("정상 PUT → 원문 그대로 저장 + .bak 생성", async ({ request }) => {
  const { absPath } = resolveManaged("telegram_channels");
  const before = await fs.readFile(absPath, "utf-8");
  try {
    const res = await request.put("/api/json-files", {
      data: { key: "telegram_channels", content: before },
    });
    expect(res.status()).toBe(200);
    const after = await fs.readFile(absPath, "utf-8");
    expect(after).toBe(before);
    // .bak 직전 버전 존재
    const bak = await fs.readFile(absPath + ".bak", "utf-8");
    expect(bak).toBe(before);
  } finally {
    await fs.rm(absPath + ".bak", { force: true });
  }
});
