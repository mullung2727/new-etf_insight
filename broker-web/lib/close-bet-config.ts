import path from "node:path";
import fs from "node:fs/promises";

// 종가베팅 전략값 config. 배치(etl/scripts/close_bet_config.py)와 같은 파일을 읽고 쓴다.
// 저장 포맷은 소수(tp/sl 0~1) — UI 는 %로 표시/입력하고 폼 계층에서 ÷100 변환해 여기로 넘긴다.
// 범위검증은 py 로더(close_bet_config._validate)와 동일 도메인. 웹 저장단·py 로더 이중 방어.
// ponytail: 로컬 단일사용자 도구라 파일 락 없음.

export type BudgetByCount = Record<"1" | "2" | "3", number>;
export type CloseBetConfig = {
  score_threshold: number;
  tp: number;
  sl: number;
  budget_by_count: BudgetByCount;
};

// 하드코딩 기본값 = py 로더 DEFAULTS 와 동일(파일 없을 때 폴백).
export const DEFAULTS: CloseBetConfig = {
  score_threshold: 70,
  tp: 0.05,
  sl: 0.03,
  budget_by_count: { "1": 3_000_000, "2": 2_000_000, "3": Math.floor(5_000_000 / 3) },
};

const COUNTS: (keyof BudgetByCount)[] = ["1", "2", "3"];

function repoRoot() {
  return path.join(process.cwd(), "..");
}
export const PATH = () => path.join(repoRoot(), "etl", "scripts", "close_bet.json");

export function validate(cfg: CloseBetConfig): void {
  const st = cfg.score_threshold;
  if (!Number.isInteger(st) || st < 0 || st > 100) {
    throw new Error("매수 기준점수는 0~100 사이 정수여야 합니다");
  }
  for (const [k, label] of [["tp", "익절"], ["sl", "손절"]] as const) {
    const v = cfg[k];
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 1) {
      throw new Error(`${label}은 0~100% 사이 값이어야 합니다`);
    }
  }
  const budget = cfg.budget_by_count;
  if (!budget || typeof budget !== "object") {
    throw new Error("종목당 예산 형식이 올바르지 않습니다");
  }
  for (const n of COUNTS) {
    const amt = budget[n];
    if (!Number.isInteger(amt) || amt <= 0) {
      throw new Error(`${n}종목일 때 예산은 0보다 큰 정수여야 합니다`);
    }
  }
}

// 파일 읽기 → 키 누락 시 기본값 폴백 → 검증. 파일 없으면 DEFAULTS.
export async function read(): Promise<CloseBetConfig> {
  let raw: Partial<CloseBetConfig> = {};
  try {
    raw = JSON.parse(await fs.readFile(PATH(), "utf-8"));
  } catch {
    return { ...DEFAULTS, budget_by_count: { ...DEFAULTS.budget_by_count } };
  }
  const cfg: CloseBetConfig = {
    score_threshold: raw.score_threshold ?? DEFAULTS.score_threshold,
    tp: raw.tp ?? DEFAULTS.tp,
    sl: raw.sl ?? DEFAULTS.sl,
    budget_by_count: raw.budget_by_count
      ? (raw.budget_by_count as BudgetByCount)
      : { ...DEFAULTS.budget_by_count },
  };
  validate(cfg);
  return cfg;
}

// 검증 통과 시에만: 직전 버전 .bak 백업 후 저장. 검증 실패 시 파일 불변(throw).
export async function write(cfg: CloseBetConfig): Promise<void> {
  validate(cfg);
  const p = PATH();
  try {
    const prev = await fs.readFile(p, "utf-8");
    await fs.writeFile(p + ".bak", prev, "utf-8");
  } catch {
    // 원본 없으면 백업 생략(최초 저장).
  }
  await fs.writeFile(p, JSON.stringify(cfg, null, 2) + "\n", "utf-8");
}
