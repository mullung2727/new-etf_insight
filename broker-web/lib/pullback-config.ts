import fs from "node:fs/promises";
import path from "node:path";

export type PullbackConfig = {
  budget_per_stock: number;
  max_new_positions: number;
  tp: number;
  sl: number;
  max_wait_days: number;
  max_hold_days: number;
};

function repoRoot() {
  return path.join(process.cwd(), "..");
}

export const PATH = () => path.join(repoRoot(), "etl", "scripts", "pullback.json");

function integer(value: number, name: string, minimum: number, maximum?: number) {
  if (!Number.isInteger(value) || value < minimum || (maximum !== undefined && value > maximum)) {
    throw new Error(`${name} 값이 허용 범위를 벗어났습니다`);
  }
}

export function validate(config: PullbackConfig): void {
  if (!config || typeof config !== "object") throw new Error("설정 형식이 올바르지 않습니다");
  integer(config.budget_per_stock, "종목당 예산", 1);
  integer(config.max_new_positions, "하루 최대 신규 매수", 1, 3);
  integer(config.max_wait_days, "관찰 거래일", 1, 5);
  integer(config.max_hold_days, "최대 보유 거래일", 1, 5);
  for (const [key, label] of [["tp", "익절"], ["sl", "손절"]] as const) {
    const value = config[key];
    if (typeof value !== "number" || !Number.isFinite(value) || value <= 0 || value > 1) {
      throw new Error(`${label}은 0% 초과 100% 이하여야 합니다`);
    }
  }
}

export async function read(target = PATH()): Promise<PullbackConfig> {
  const config = JSON.parse(await fs.readFile(target, "utf-8")) as PullbackConfig;
  validate(config);
  return config;
}

export async function write(config: PullbackConfig, target = PATH()): Promise<void> {
  validate(config);
  const previous = await fs.readFile(target, "utf-8");
  await fs.writeFile(target + ".bak", previous, "utf-8");
  await fs.writeFile(target, JSON.stringify(config, null, 2) + "\n", "utf-8");
}
