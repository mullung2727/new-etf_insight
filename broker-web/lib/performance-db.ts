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
  bt: { start: string; end: string; liveRet: number; btRet: number; diffPp: number } | null;
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

// 같은 구간 실매매·백테스트 수익률 비교. bt 가 비었거나 겹치는 날짜가 없으면 null.
// liveRet 는 live 전체 twrIndex 기준 idx(end) / idx(start 전날) − 1 (start 전날 행이 없으면 분모 1).
export function compareWindow(
  live: (TwrInput & { date: string })[],
  bt: (TwrInput & { date: string })[]
): { start: string; end: string; liveRet: number; btRet: number; diffPp: number } | null {
  if (bt.length === 0) return null;
  const start = bt[0].date;
  const liveDates = new Set(live.map((r) => r.date));
  const common = bt.filter((r) => liveDates.has(r.date));
  if (common.length === 0) return null;
  const end = common[common.length - 1].date;
  const btIdx = twrIndex(bt.filter((r) => r.date <= end));
  const btRet = btIdx[btIdx.length - 1] - 1;
  const liveIdx = twrIndex(live);
  const endPos = live.findIndex((r) => r.date === end);
  let base = 1;
  for (let i = 0; i < live.length; i++) {
    if (live[i].date < start) base = liveIdx[i];
  }
  const liveRet = liveIdx[endPos] / base - 1;
  return { start, end, liveRet, btRet, diffPp: (liveRet - btRet) * 100 };
}

// 차트 겹침용 시리즈. bt 곡선을 실매매 곡선의 bt 시작 전날 값에 붙인다.
export function overlaySeries(
  live: (TwrInput & { date: string })[],
  bt: (TwrInput & { date: string })[]
): { date: string; livePct: number; btPct: number | null }[] {
  const liveIdx = twrIndex(live);
  const btIdx = twrIndex(bt);
  const btMap = new Map<string, number>(bt.map((r, i) => [r.date, btIdx[i]]));
  let base = 1;
  if (bt.length > 0) {
    const start = bt[0].date;
    for (let i = 0; i < live.length; i++) {
      if (live[i].date < start) base = liveIdx[i];
    }
  }
  return live.map((r, i) => {
    const b = btMap.get(r.date);
    return {
      date: r.date,
      livePct: (liveIdx[i] - 1) * 100,
      btPct: b === undefined ? null : (base * b - 1) * 100,
    };
  });
}

function getDbPath(): string {
  const path = process.env.PERFORMANCE_DB;
  if (!path) throw new Error("PERFORMANCE_DB 환경변수가 설정되지 않았습니다.");
  return path;
}

export function getDaily(strategy: string, kind: "live" | "bt" = "live"): DailyRow[] {
  const db = new DatabaseSync(getDbPath(), { readOnly: true });
  try {
    return db
      .prepare(
        "SELECT date, strategy, seed, flow, flow_note, equity, realized_cum, unrealized, open_n, trades_cum" +
          " FROM strategy_daily WHERE kind = ? AND strategy = ? ORDER BY date ASC"
      )
      .all(kind, strategy) as unknown as DailyRow[];
  } finally {
    db.close();
  }
}

export function getSummaries(): StrategySummary[] {
  return STRATEGIES.map(({ key, label, currency }) => {
    const rows = getDaily(key);
    const bt = compareWindow(rows, getDaily(key, "bt"));
    if (rows.length === 0) {
      return {
        key, label, currency,
        lastDate: null, seed: null, equity: null, cumTwr: null,
        mdd: null, tradesCum: null, openN: null, bt,
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
      bt,
    };
  });
}
