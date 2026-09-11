import path from "node:path";
import fs from "node:fs/promises";

// 종가베팅 전략값 config. 배치(etl/scripts/close_bet_config.py)와 같은 파일을 읽고 쓴다.
// 저장 포맷은 소수(tp/sl 0~1) — UI 는 %로 표시/입력하고 폼 계층에서 ÷100 변환해 여기로 넘긴다.
// 범위검증은 py 로더(close_bet_config._validate)와 동일 도메인. 웹 저장단·py 로더 이중 방어.
// ponytail: 로컬 단일사용자 도구라 파일 락 없음.

export type BudgetByCount = Record<"1" | "2" | "3", number>;
export type CloseBetConfig = {
  score_threshold: number;
  tp: number | null; // null = 장중 TP/SL 판정 끔(익일 강제청산만)
  sl: number | null;
  cap_max: number; // 원. 시총 상한(미만)
  turnover_min: number; // 원. 전일 거래대금 하한(이상)
  cap_min_pct: number; // 0~1(1 미만). 전일 전 종목 시총 하위 이 비율은 15시 스코어링에서 제외. 0 = 끔
  exit_time: string; // "HH:MM:SS". 청산 워커의 강제청산 시각(단일 소스)
  budget_by_count: BudgetByCount;
};

// 하드코딩 기본값 = py 로더 DEFAULTS 와 동일(파일 없을 때 폴백).
export const DEFAULTS: CloseBetConfig = {
  score_threshold: 70,
  tp: 0.05,
  sl: 0.03,
  // 자리표시자. 실제 운영값은 close_bet.json(gitignore) — py 로더 DEFAULTS 와 같은 규칙.
  cap_max: 10_000_000_000_000,
  turnover_min: 1,
  cap_min_pct: 0,
  exit_time: "15:19:00",
  budget_by_count: { "1": 3_000_000, "2": 2_000_000, "3": Math.floor(5_000_000 / 3) },
};

const COUNTS: (keyof BudgetByCount)[] = ["1", "2", "3"];

function repoRoot() {
  return path.join(process.cwd(), "..");
}
export const PATH = () => path.join(repoRoot(), "etl", "scripts", "close_bet.json");

// "HH:MM:SS" 이고 실재하는 시각인지 (py 로더 _is_hms 와 같은 도메인).
export function isHms(v: unknown): boolean {
  if (typeof v !== "string" || !/^\d{2}:\d{2}:\d{2}$/.test(v)) return false;
  const [h, m, s] = v.split(":").map(Number);
  return h <= 23 && m <= 59 && s <= 59;
}

export function validate(cfg: CloseBetConfig): void {
  const st = cfg.score_threshold;
  if (!Number.isInteger(st) || st < 0 || st > 100) {
    throw new Error("매수 기준점수는 0~100 사이 정수여야 합니다");
  }
  for (const [k, label] of [["tp", "익절"], ["sl", "손절"]] as const) {
    const v = cfg[k];
    if (v === null) continue; // 빈칸 = 사용 안 함
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 1) {
      throw new Error(`${label}은 0~100% 사이 값이거나 비어 있어야 합니다`);
    }
  }
  for (const [k, label] of [["cap_max", "시가총액 상한"], ["turnover_min", "거래대금 하한"]] as const) {
    const v = cfg[k];
    if (!Number.isInteger(v) || v <= 0) {
      throw new Error(`${label}은 0보다 큰 금액이어야 합니다`);
    }
  }
  const pct = cfg.cap_min_pct;
  if (typeof pct !== "number" || !Number.isFinite(pct) || pct < 0 || pct >= 1) {
    throw new Error("시가총액 하한(하위 %)은 0 이상 100 미만이어야 합니다");
  }
  if (!isHms(cfg.exit_time)) {
    throw new Error("청산 시각은 HH:MM:SS 형식이어야 합니다 (예: 09:01:00)");
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
    // null 은 "끔"이라 ?? 로 기본값을 덮으면 안 된다 — 키 존재 여부로 판단한다.
    tp: "tp" in raw ? (raw.tp as number | null) : DEFAULTS.tp,
    sl: "sl" in raw ? (raw.sl as number | null) : DEFAULTS.sl,
    cap_max: raw.cap_max ?? DEFAULTS.cap_max,
    turnover_min: raw.turnover_min ?? DEFAULTS.turnover_min,
    cap_min_pct: raw.cap_min_pct ?? DEFAULTS.cap_min_pct,
    exit_time: raw.exit_time ?? DEFAULTS.exit_time,
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
