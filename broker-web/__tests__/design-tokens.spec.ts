import { test, expect } from "@playwright/test";
import fs from "fs";
import path from "path";

const ROOT = path.resolve(__dirname, "..");

function readFilesRecursive(dir: string, exts: string[]): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name === ".next") continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...readFilesRecursive(full, exts));
    else if (exts.some((e) => entry.name.endsWith(e))) out.push(full);
  }
  return out;
}

// de-hardcode 게이트 대상: app/ + components/ (tsx/ts)
// 예외(토큰 정의처): globals.css, lib/tokens.ts — 유일 hex 허용처
function targetFiles(): string[] {
  return [
    ...readFilesRecursive(path.join(ROOT, "app"), [".tsx", ".ts"]),
    ...readFilesRecursive(path.join(ROOT, "components"), [".tsx", ".ts"]),
  ];
}

const GATES: { name: string; re: RegExp }[] = [
  {
    name: "arbitrary-hex 클래스",
    re: /(text|bg|border|ring|fill|stroke|from|to|via)-\[#[0-9a-fA-F]{3,8}\]/,
  },
  {
    name: "네임드 팔레트색",
    re: /(text|bg|border|ring|from|to|via|fill|stroke)-(red|blue|green|emerald|amber|yellow|orange|slate|gray|zinc|neutral|indigo|violet|purple|pink|rose|cyan|teal|sky)-[0-9]{2,3}/,
  },
  {
    name: "인라인 style hex(#rrggbb)",
    re: /#[0-9a-fA-F]{6}\b/,
  },
];

for (const gate of GATES) {
  test(`하드코딩 게이트: ${gate.name} = 0`, () => {
    const offenders: string[] = [];
    for (const f of targetFiles()) {
      const rel = path.relative(ROOT, f);
      const lines = fs.readFileSync(f, "utf8").split("\n");
      lines.forEach((line, i) => {
        if (gate.re.test(line)) offenders.push(`${rel}:${i + 1}  ${line.trim()}`);
      });
    }
    expect(offenders, `\n${offenders.join("\n")}\n`).toEqual([]);
  });
}

test("globals.css: Sentry 다크 토큰 + 신규 토큰 정의", () => {
  const css = fs.readFileSync(path.join(ROOT, "app/globals.css"), "utf8");
  const required = [
    /--background:\s*#1f1633/,
    /--card:\s*#150f23/,
    /--primary:\s*#150f23/,
    /--ring:\s*#c2ef4e/,
    /--fin-gold:\s*#c2ef4e/,
    /--buy:/,
    /--sell:/,
    /--color-buy:/,
    /--color-sell:/,
    /--font-display:/,
  ];
  const missing = required.filter((re) => !re.test(css)).map((re) => re.source);
  expect(missing, `\nmissing tokens:\n${missing.join("\n")}\n`).toEqual([]);
});

test("layout.tsx: Rubik + Space Grotesk 폰트 로드", () => {
  const layout = fs.readFileSync(path.join(ROOT, "app/layout.tsx"), "utf8");
  expect(layout).toMatch(/Rubik/);
  expect(layout).toMatch(/Space_Grotesk/);
});
