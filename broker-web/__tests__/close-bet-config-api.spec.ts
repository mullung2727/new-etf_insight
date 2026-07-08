import { test, expect } from "@playwright/test";
import fs from "node:fs/promises";
import { PATH } from "../lib/close-bet-config";

// 실제 close_bet.json 을 건드리므로 스냅샷 후 복원. .bak 도 정리.
let before: string;

test.beforeAll(async () => {
  before = await fs.readFile(PATH(), "utf-8");
});
test.afterAll(async () => {
  await fs.writeFile(PATH(), before, "utf-8");
  await fs.rm(PATH() + ".bak", { force: true });
});

const VALID = {
  score_threshold: 60,
  tp: 0.07,
  sl: 0.04,
  budget_by_count: { "1": 3000000, "2": 2000000, "3": 1666666 },
};

test.describe.serial("종가베팅 config", () => {
  test("GET → 4개 키 구조", async ({ request }) => {
    const res = await request.get("/api/close-bet-config");
    expect(res.status()).toBe(200);
    const b = await res.json();
    expect(typeof b.score_threshold).toBe("number");
    expect(typeof b.tp).toBe("number");
    expect(typeof b.sl).toBe("number");
    expect(b.budget_by_count).toHaveProperty("1");
  });

  test("PUT 범위초과(score 150) → 400·파일 불변", async ({ request }) => {
    const raw = await fs.readFile(PATH(), "utf-8");
    const res = await request.put("/api/close-bet-config", {
      data: { ...VALID, score_threshold: 150 },
    });
    expect(res.status()).toBe(400);
    expect(await fs.readFile(PATH(), "utf-8")).toBe(raw); // 저장 안 됨
  });

  test("PUT 음수 예산 → 400", async ({ request }) => {
    const res = await request.put("/api/close-bet-config", {
      data: { ...VALID, budget_by_count: { "1": 3000000, "2": -1, "3": 1666666 } },
    });
    expect(res.status()).toBe(400);
  });

  test("PUT 정상 → 200·재로드 반영·.bak 생성", async ({ request }) => {
    const res = await request.put("/api/close-bet-config", { data: VALID });
    expect(res.status()).toBe(200);
    const b = await res.json();
    expect(b.score_threshold).toBe(60);
    expect(b.tp).toBe(0.07);
    // .bak 존재
    await expect(fs.access(PATH() + ".bak")).resolves.toBeUndefined();
  });
});
