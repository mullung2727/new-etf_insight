// 서버 전용 — 'use client' 파일에서 import 금지. DB 는 Next 서버에서만 읽는다(브라우저로 DB 경로·파일 안 나감).

import { DatabaseSync } from "node:sqlite";

export interface DailyRow {
  date: string;
  strategy: string;
  seed: number;
  flow: number;
  flow_note: string | null;
  equity: number;
  realized_cum: number;
  unrealized: number;
  open_n: number;
  trades_cum: number;
}

export interface StrategyMeta {
  key: string;
  label: string;
  currency: "KRW" | "USD";
}

export const STRATEGIES: StrategyMeta[] = [
  { key: "high52", label: "52주 신고가", currency: "KRW" },
  { key: "rebound", label: "되돌림", currency: "KRW" },
  { key: "rights_dip", label: "유무상증자", currency: "KRW" },
  { key: "msig", label: "초소형 저평가", currency: "KRW" },
  { key: "us_v38", label: "미국 TQQQ v38", currency: "USD" },
];

export interface StrategySummary {
  key: string;
  label: string;
  currency: "KRW" | "USD";
  lastDate: string | null;
  seed: number | null;
  equity: number | null;
  cumTwr: number | null;
  mdd: number | null;
  tradesCum: number | null;
  openN: number | null;
}

type TwrInput = { equity: number; flow: number };

// 파이썬 etl/scripts/strategy_snapshot.py 의 twr·twr_mdd 와 동일한 식.
export function twr(rows: TwrInput[]): number[] {
  const out: number[] = [];
  let prev: number | null = null;
  for (const row of rows) {
    if (prev === null) {
      out.push(row.flow ? (row.equity - row.flow) / row.flow : 0);
    } else if (!prev) {
      out.push(0);
    } else {
      out.push((row.equity - row.flow) / prev - 1);
    }
    prev = row.equity;
  }
  return out;
}

export function twrIndex(rows: TwrInput[]): number[] {
  const idx: number[] = [];
  let acc = 1;
  for (const r of twr(rows)) {
    acc *= 1 + r;
    idx.push(acc);
  }
  return idx;
}

export function twrMdd(rows: TwrInput[]): number {
  let idx = 1;
  let peak = 1;
  let mdd = 0;
  for (const r of twr(rows)) {
    idx *= 1 + r;
    peak = Math.max(peak, idx);
    if (peak > 0) {
      mdd = Math.min(mdd, idx / peak - 1);
    }
  }
  return mdd;
}

function getDbPath(): string {
  const path = process.env.PERFORMANCE_DB;
  if (!path) throw new Error("PERFORMANCE_DB 환경변수가 설정되지 않았습니다.");
  return path;
}

export function getDaily(strategy: string): DailyRow[] {
  const db = new DatabaseSync(getDbPath(), { readOnly: true });
  try {
    return db
      .prepare(
        "SELECT date, strategy, seed, flow, flow_note, equity, realized_cum, unrealized, open_n, trades_cum" +
          " FROM strategy_daily WHERE kind = 'live' AND strategy = ? ORDER BY date ASC"
      )
      .all(strategy) as unknown as DailyRow[];
  } finally {
    db.close();
  }
}

export function getSummaries(): StrategySummary[] {
  return STRATEGIES.map(({ key, label, currency }) => {
    const rows = getDaily(key);
    if (rows.length === 0) {
      return {
        key, label, currency,
        lastDate: null, seed: null, equity: null, cumTwr: null,
        mdd: null, tradesCum: null, openN: null,
      };
    }
    const last = rows[rows.length - 1];
    const idx = twrIndex(rows);
    return {
      key,
      label,
      currency,
      lastDate: last.date,
      seed: last.seed,
      equity: last.equity,
      cumTwr: idx[idx.length - 1] - 1,
      mdd: twrMdd(rows),
      tradesCum: last.trades_cum,
      openN: last.open_n,
    };
  });
}
