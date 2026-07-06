import { test, expect } from "@playwright/test";
import path from "node:path";
import { MANAGED_FILES, findManaged, resolveManaged } from "../lib/json-admin";

test("등록된 key는 repo-root 기준 절대경로로 해석", () => {
  const { file, absPath } = resolveManaged("cron_registry");
  expect(file.risk).toBe("caution");
  expect(path.isAbsolute(absPath)).toBe(true);
  expect(absPath.replace(/\\/g, "/")).toContain("ops/batches/openclaw-cron.registry.json");
});

test("미등록 key는 throw, findManaged는 undefined", () => {
  expect(() => resolveManaged("../../etc/passwd")).toThrow();
  expect(() => resolveManaged("nope")).toThrow();
  expect(findManaged("nope")).toBeUndefined();
});

test("관리 파일 3개 모두 label·relPath·description 보유", () => {
  expect(MANAGED_FILES).toHaveLength(3);
  for (const f of MANAGED_FILES) {
    expect(f.label.length).toBeGreaterThan(0);
    expect(f.description.length).toBeGreaterThan(0);
    expect(f.relPath).toMatch(/\.json$/);
    expect(["safe", "caution"]).toContain(f.risk);
  }
});
