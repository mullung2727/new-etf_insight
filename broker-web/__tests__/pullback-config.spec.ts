import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { read, validate, write, type PullbackConfig } from "../lib/pullback-config";

const VALID: PullbackConfig = {
  budget_per_stock: 300000,
  max_new_positions: 3,
  tp: 0.03,
  sl: 0.03,
  max_wait_days: 5,
  max_hold_days: 3,
};

test("설정 검증 범위는 Python 로더와 일치", () => {
  expect(() => validate(VALID)).not.toThrow();
  for (const invalid of [
    { ...VALID, budget_per_stock: 0 },
    { ...VALID, max_new_positions: 4 },
    { ...VALID, tp: 0 },
    { ...VALID, sl: 1.1 },
    { ...VALID, max_wait_days: 6 },
    { ...VALID, max_hold_days: 0 },
  ]) {
    expect(() => validate(invalid)).toThrow();
  }
});

test("임시 JSON 저장은 백업을 만들고 재로드된다", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "pullback-config-"));
  const target = path.join(dir, "pullback.json");
  await fs.writeFile(target, JSON.stringify(VALID), "utf-8");
  const updated = { ...VALID, budget_per_stock: 250000 };
  await write(updated, target);
  expect(await read(target)).toEqual(updated);
  await expect(fs.access(target + ".bak")).resolves.toBeUndefined();
  await fs.rm(dir, { recursive: true, force: true });
});

test("필수 키 누락 저장은 원본을 바꾸지 않는다", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "pullback-config-"));
  const target = path.join(dir, "pullback.json");
  const before = JSON.stringify(VALID);
  await fs.writeFile(target, before, "utf-8");
  await expect(write({ ...VALID, budget_per_stock: undefined } as unknown as PullbackConfig, target)).rejects.toThrow();
  expect(await fs.readFile(target, "utf-8")).toBe(before);
  await fs.rm(dir, { recursive: true, force: true });
});

test("API GET과 설정 탭이 노출된다", async ({ request, page }) => {
  const response = await request.get("/api/pullback-config");
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual(VALID);
  await page.goto("/admin/settings");
  await expect(page.getByRole("button", { name: "눌림목 매매" })).toBeVisible();
});
