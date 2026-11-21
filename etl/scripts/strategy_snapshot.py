import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import _bootstrap  # noqa: F401

"""전략 일별 스냅샷: 공통 평가 엔진 + 전략 어댑터 (PLAN_STRATEGY_PERFORMANCE P1).

원장 체결 → (확정 손익, 보유×종가) 일별 평가액을 performance.sqlite3 에 저장.
수수료·세금 내역은 확정 손익에만 반영, 시드 변화는 flow 로 분리해 TWR 로 본다.

Usage (from etl/):
    uv run python scripts/strategy_snapshot.py [--strategy rebound] [--end YYYYMMDD] [--check] [--dry-run]
"""
import argparse
import json
import sqlite3
from pathlib import Path

import duckdb

from new_etf_insight.data_paths import db_dir
from strategy_budget import STRATEGIES, _resolve_config
from trading_batch_common import now_seoul
from wl_sqlite import connect_ro, connect_rw

# 국내 편도 수수료 0.015%, 매도 거래세+농특세 0.20% — research/rl_signal/costs.py, BACKTEST_DATA.md 6-1 실측
FEE = 0.00015
SELL_TAX = 0.0020

PERF_DB = db_dir() / "performance.sqlite3"
WL_DB = db_dir() / "watchlist.sqlite3"
KRX_DB = db_dir() / "krx_ohlcv.duckdb"

_SCHEMA_DAILY = """CREATE TABLE IF NOT EXISTS strategy_daily(
  date TEXT, strategy TEXT, kind TEXT,
  seed REAL, flow REAL, flow_note TEXT,
  equity REAL, realized_cum REAL, unrealized REAL, pnl REAL,
  open_n INTEGER, trades_cum INTEGER, updated_at TEXT,
  PRIMARY KEY(date, strategy, kind))"""

_SCHEMA_SEED = """CREATE TABLE IF NOT EXISTS strategy_seed(
  strategy TEXT, date TEXT, capital REAL, since TEXT, note TEXT,
  PRIMARY KEY(strategy, date))"""


def ensure(perf_db: str | Path) -> None:
    """performance.sqlite3 스키마 보장. main·저장 함수에서만 호출 (import 시 생성 금지)."""
    with connect_rw(perf_db) as con:
        con.execute(_SCHEMA_DAILY)
        con.execute(_SCHEMA_SEED)


def compute(
    fills: list[dict],
    seed_events: list[dict],
    closes: dict[tuple[str, str], float],
    days: list[str],
) -> list[dict]:
    """공통 평가 엔진. fills/seed_events/closes 주입 → 일별 행 리스트."""
    by_date: dict[str, list[dict]] = {}
    for fill in fills:
        by_date.setdefault(str(fill["date"]), []).append(fill)
    for day_fills in by_date.values():
        day_fills.sort(key=lambda f: 0 if f["side"] == "buy" else 1)
    events = sorted(seed_events, key=lambda e: e["date"])

    held: dict[str, int] = {}
    cost: dict[str, float] = {}
    last_close: dict[str, float] = {}
    realized: list[tuple[str, float]] = []
    rows: list[dict] = []
    prev_equity: float | None = None
    prev_unreal = 0.0
    trades_cum = 0
    for i, day in enumerate(days):
        for fill in by_date.get(day, []):
            ticker = str(fill["ticker"])
            qty = fill["qty"]
            price = fill["price"]
            if qty is None or price is None or qty <= 0:
                continue
            if fill["side"] == "buy":
                cost[ticker] = cost.get(ticker, 0.0) + qty * price * (1 + FEE)
                held[ticker] = held.get(ticker, 0) + qty
            elif fill["side"] == "sell":
                have = held.get(ticker, 0)
                if have < qty:
                    raise ValueError(f"보유 부족 매도: {day} {ticker} 보유 {have} < 매도 {qty}")
                avg = cost.get(ticker, 0.0) / have if have else 0.0
                proceeds = qty * price * (1 - FEE - SELL_TAX)
                realized.append((day, proceeds - avg * qty))
                cost[ticker] = cost.get(ticker, 0.0) - avg * qty
                held[ticker] = have - qty
                trades_cum += 1
        if events:
            active = events[0]
            for event in events:
                if event["date"] <= day:
                    active = event
                else:
                    break
        else:
            active = {"capital": 0.0, "since": ""}
        capital = float(active["capital"])
        since = active["since"]
        realized_cum = sum(amount for d, amount in realized if since <= d <= day)
        day_real = sum(amount for d, amount in realized if d == day and since <= d)
        unrealized = 0.0
        open_n = 0
        for ticker, qty in held.items():
            if qty <= 0:
                continue
            open_n += 1
            if (day, ticker) in closes:
                last_close[ticker] = closes[(day, ticker)]
            px = last_close.get(ticker)
            if px is None:
                continue  # 종가 전무 → 평균원가 평가 (미실현 기여 0)
            unrealized += qty * px - cost.get(ticker, 0.0)
        equity = capital + realized_cum + unrealized
        pnl = day_real + unrealized - prev_unreal
        if i == 0:
            flow: float = equity
            flow_note = "시작"
        else:
            flow = equity - prev_equity - pnl
            flow_note = ""
            for event in events:
                if event["date"] == day:
                    flow_note = event.get("note", "")
        if abs(flow) < 0.5:
            flow = 0.0
        rows.append({
            "date": day, "seed": capital, "flow": flow, "flow_note": flow_note,
            "equity": equity, "realized_cum": realized_cum, "unrealized": unrealized,
            "pnl": pnl, "open_n": open_n, "trades_cum": trades_cum,
        })
        prev_equity = equity
        prev_unreal = unrealized
    return rows


def twr(rows: list[dict]) -> list[float]:
    """일별 시간가중 수익률. r_D = (equity − flow)/전날 equity − 1 (첫날 0)."""
    out: list[float] = []
    prev: float | None = None
    for row in rows:
        if prev is None or not prev:
            out.append(0.0)
        else:
            out.append((row["equity"] - row["flow"]) / prev - 1)
        prev = row["equity"]
    return out


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,),
    ).fetchone()
    return row is not None


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _qty(value) -> int:
    if value is None:
        return 0
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def rebound_fills(wl_con: sqlite3.Connection, days: list[str]) -> list[dict]:
    """rebound_positions → Fill 리스트. carried 매도는 days 기준 다음 거래일로."""
    if not _table_exists(wl_con, "rebound_positions"):
        return []
    cols = _columns(wl_con, "rebound_positions")
    if "date" not in cols or "ticker" not in cols:
        return []
    wanted = ["date", "ticker", "fq1", "fp1", "fq2", "fp2", "fq3", "fp3",
              "sell_qty", "sell_px", "sell_status", "reason"]
    avail = [c for c in wanted if c in cols]
    try:
        db_rows = wl_con.execute(
            f"SELECT {','.join(avail)} FROM rebound_positions ORDER BY date").fetchall()
    except sqlite3.Error:
        return []
    idx = {name: pos for pos, name in enumerate(avail)}

    def get(row, name: str):
        return row[idx[name]] if name in idx else None

    next_day = {d: days[i + 1] for i, d in enumerate(days) if i + 1 < len(days)}
    fills: list[dict] = []
    for row in db_rows:
        raw_date, raw_ticker = get(row, "date"), get(row, "ticker")
        if raw_date is None or raw_ticker is None:
            continue
        day, ticker = str(raw_date), str(raw_ticker)
        ref = f"rebound:{day}:{ticker}"
        for n in (1, 2, 3):
            fq = _qty(get(row, f"fq{n}"))
            fp = _num(get(row, f"fp{n}"))
            if fq > 0 and fp is not None and fp > 0:
                fills.append({"date": day, "ticker": ticker, "side": "buy",
                              "qty": fq, "price": fp, "ref": ref})
        sq = _qty(get(row, "sell_qty"))
        sp = _num(get(row, "sell_px"))
        if sq > 0 and sp is not None and sp > 0:
            carried = get(row, "sell_status") == "carried" or get(row, "reason") == "carried"
            sell_day = next_day.get(day, day) if carried else day
            fills.append({"date": sell_day, "ticker": ticker, "side": "sell",
                          "qty": sq, "price": sp, "ref": ref})
    return fills


def rebound_seed_events(wl_con: sqlite3.Connection, first_date: str) -> list[dict]:
    """strategy_budget_resets(rebound) + msig_settlements → SeedEvent 리스트."""
    first_date = str(first_date)
    events: list[dict] = []
    resets: list[tuple[str, float | None, float, str]] = []
    if _table_exists(wl_con, "strategy_budget_resets"):
        try:
            db_rows = wl_con.execute(
                "SELECT old_capital, new_capital, since, note"
                " FROM strategy_budget_resets WHERE strategy='rebound'"
                " ORDER BY date, rowid").fetchall()
        except sqlite3.Error:
            db_rows = []
        for old, new, since, note in db_rows:
            new_f = _num(new)
            if since is None or new_f is None:
                continue
            resets.append((str(since), _num(old), new_f, note or ""))
    for since, _old, new_f, note in resets:
        events.append({"date": since, "capital": new_f, "since": since, "note": note})
    if resets and resets[0][1] is not None and resets[0][1] > 0 \
            and first_date < resets[0][0]:
        events.insert(0, {"date": first_date, "capital": resets[0][1],
                          "since": first_date, "note": "리셋 전 시드"})
    if _table_exists(wl_con, "msig_settlements"):
        cols = _columns(wl_con, "msig_settlements")
        if "date" in cols and "rebound_capital" in cols:
            try:
                settle_rows = wl_con.execute(
                    "SELECT date, rebound_capital FROM msig_settlements ORDER BY date"
                ).fetchall()
            except sqlite3.Error:
                settle_rows = []
            for raw_date, raw_cap in settle_rows:
                cap = _num(raw_cap)
                if raw_date is None or cap is None:
                    continue
                sdate = str(raw_date)
                prior = None
                for event in sorted(events, key=lambda e: e["date"]):
                    if event["date"] <= sdate:
                        prior = event
                events.append({"date": sdate, "capital": cap,
                               "since": prior["since"] if prior else sdate,
                               "note": "msig 정산 이체"})
    events.sort(key=lambda e: e["date"])
    return events


def realized_check(wl_con: sqlite3.Connection) -> list[dict]:
    """행별 엔진 실현 vs 키움 realized 대조 → {date,ticker,ours,kiwoom,diff}."""
    if not _table_exists(wl_con, "rebound_positions"):
        return []
    cols = _columns(wl_con, "rebound_positions")
    need = {"date", "ticker", "sell_qty", "sell_px", "realized"}
    if not need <= cols:
        return []
    wanted = ["date", "ticker", "fq1", "fp1", "fq2", "fp2", "fq3", "fp3",
              "sell_qty", "sell_px", "realized"]
    avail = [c for c in wanted if c in cols]
    try:
        db_rows = wl_con.execute(
            f"SELECT {','.join(avail)} FROM rebound_positions"
            " WHERE realized IS NOT NULL ORDER BY date").fetchall()
    except sqlite3.Error:
        return []
    idx = {name: pos for pos, name in enumerate(avail)}

    def get(row, name: str):
        return row[idx[name]] if name in idx else None

    out: list[dict] = []
    for row in db_rows:
        sq = _qty(get(row, "sell_qty"))
        sp = _num(get(row, "sell_px"))
        kiwoom = _num(get(row, "realized"))
        if sq <= 0 or sp is None or sp <= 0 or kiwoom is None:
            continue
        buy_qty = 0
        buy_cost = 0.0
        for n in (1, 2, 3):
            fq = _qty(get(row, f"fq{n}"))
            fp = _num(get(row, f"fp{n}"))
            if fq > 0 and fp is not None and fp > 0:
                buy_qty += fq
                buy_cost += fq * fp * (1 + FEE)
        if buy_qty <= 0:
            continue
        ours = sq * sp * (1 - FEE - SELL_TAX) - buy_cost / buy_qty * sq
        out.append({"date": str(get(row, "date")), "ticker": str(get(row, "ticker")),
                    "ours": ours, "kiwoom": kiwoom, "diff": ours - kiwoom})
    return out


def log_seed(perf_db: str | Path, strategy: str, date: str, capital, since: str) -> bool:
    """config 시드가 전략 마지막 기록과 다르면 strategy_seed 에 남기고 True."""
    ensure(perf_db)
    with connect_rw(perf_db) as con:
        last = con.execute(
            "SELECT capital FROM strategy_seed WHERE strategy=? ORDER BY date DESC LIMIT 1",
            (strategy,)).fetchone()
        if last is not None and last[0] == capital:
            return False
        con.execute(
            "INSERT OR REPLACE INTO strategy_seed(strategy, date, capital, since, note)"
            " VALUES (?,?,?,?,?)",
            (strategy, str(date), capital, str(since) if since is not None else None,
             "config"))
        return True


def load_config_seed_events(perf_db: str | Path, strategy: str) -> list[dict]:
    """strategy_seed 행 → SeedEvent 리스트 (note 'config')."""
    if not Path(perf_db).exists():
        return []
    try:
        with connect_ro(perf_db) as con:
            if not _table_exists(con, "strategy_seed"):
                return []
            db_rows = con.execute(
                "SELECT date, capital, since FROM strategy_seed"
                " WHERE strategy=? ORDER BY date", (strategy,)).fetchall()
    except sqlite3.Error:
        return []
    events = []
    for raw_date, capital, since in db_rows:
        cap = _num(capital)
        if raw_date is None or cap is None:
            continue
        day = str(raw_date)
        events.append({"date": day, "capital": cap,
                       "since": str(since) if since is not None else day,
                       "note": "config"})
    return events


def merge_seed_events(adapter_events: list[dict], config_events: list[dict]) -> list[dict]:
    """어댑터 이벤트 + config 행 병합. 같은 date 면 어댑터 우선, 중복 capital 은 버림."""
    by_date = {e["date"] for e in adapter_events}
    ordered = sorted(adapter_events, key=lambda e: e["date"])
    merged = list(adapter_events)
    for cfg in config_events:
        effective = None
        for event in ordered:
            if event["date"] <= cfg["date"]:
                effective = event
        if effective is not None and effective["capital"] == cfg["capital"]:
            continue
        if cfg["date"] in by_date:
            continue
        merged.append(cfg)
    merged.sort(key=lambda e: e["date"])
    return merged


def upsert(perf_db: str | Path, strategy: str, kind: str, rows: list[dict]) -> None:
    """strategy_daily 에 일별 행 저장 (충돌 시 갱신)."""
    if not rows:
        return
    ensure(perf_db)
    now = now_seoul().isoformat()
    with connect_rw(perf_db) as con:
        con.executemany(
            "INSERT INTO strategy_daily(date, strategy, kind, seed, flow, flow_note,"
            " equity, realized_cum, unrealized, pnl, open_n, trades_cum, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(date, strategy, kind) DO UPDATE SET"
            " seed=excluded.seed, flow=excluded.flow, flow_note=excluded.flow_note,"
            " equity=excluded.equity, realized_cum=excluded.realized_cum,"
            " unrealized=excluded.unrealized, pnl=excluded.pnl, open_n=excluded.open_n,"
            " trades_cum=excluded.trades_cum, updated_at=excluded.updated_at",
            [(row["date"], strategy, kind, row["seed"], row["flow"], row["flow_note"],
              row["equity"], row["realized_cum"], row["unrealized"], row["pnl"],
              row["open_n"], row["trades_cum"], now) for row in rows])


def load_days(krx_db: str | Path, start: str, end: str) -> list[str]:
    """krx_ohlcv 거래일 오름차순."""
    con = duckdb.connect(str(krx_db), read_only=True)
    try:
        db_rows = con.execute(
            "SELECT DISTINCT date FROM ohlcv WHERE date BETWEEN ? AND ? ORDER BY date",
            [start, end]).fetchall()
    finally:
        con.close()
    return [row[0] for row in db_rows]


def load_closes(krx_db: str | Path, tickers: list[str],
               start: str, end: str) -> dict[tuple[str, str], float]:
    """{(date, ticker): close}."""
    if not tickers:
        return {}
    placeholders = ",".join("?" for _ in tickers)
    con = duckdb.connect(str(krx_db), read_only=True)
    try:
        db_rows = con.execute(
            "SELECT date, ticker, close FROM ohlcv"
            f" WHERE date BETWEEN ? AND ? AND ticker IN ({placeholders})",
            [start, end, *tickers]).fetchall()
    finally:
        con.close()
    return {(row[0], row[1]): row[2] for row in db_rows}


ADAPTERS = {"rebound": (rebound_fills, rebound_seed_events)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="전략 일별 스냅샷 (실매매 live)")
    parser.add_argument("--strategy", choices=sorted(ADAPTERS), default=None)
    parser.add_argument("--end", default=None, help="YYYYMMDD (기본 오늘 KST)")
    parser.add_argument("--check", action="store_true", help="realized 대조 표 출력")
    parser.add_argument("--dry-run", action="store_true", help="저장 안 함")
    args = parser.parse_args(argv)
    end = args.end or now_seoul().strftime("%Y%m%d")
    if not args.dry_run:
        ensure(PERF_DB)
    names = [args.strategy] if args.strategy else sorted(ADAPTERS)
    for name in names:
        fills_fn, events_fn = ADAPTERS[name]
        config = json.loads(_resolve_config(STRATEGIES[name][1]).read_text(encoding="utf-8"))
        capital = config.get("capital")
        if capital is None:
            print(f"{name}: config capital 없음 — 건너뜀")
            continue
        if not args.dry_run:
            log_seed(PERF_DB, name, end, capital, config.get("capital_since"))
        with connect_ro(WL_DB) as wl_con:
            probe = fills_fn(wl_con, [])
            if not probe:
                print(f"{name}: 체결 없음")
                continue
            first = min(fill["date"] for fill in probe)
            days = load_days(KRX_DB, first, end)
            if not days:
                print(f"{name}: 거래일 없음 ({first}~{end})")
                continue
            fills = fills_fn(wl_con, days)
            events = events_fn(wl_con, first)
            check_rows = realized_check(wl_con) if args.check and name == "rebound" else None
        tickers = sorted({fill["ticker"] for fill in fills})
        closes = load_closes(KRX_DB, tickers, first, end)
        merged = merge_seed_events(events, load_config_seed_events(PERF_DB, name))
        rows = compute(fills, merged, closes, days)
        if not args.dry_run:
            upsert(PERF_DB, name, "live", rows)
        if check_rows is not None:
            print(f"[{name} realized 대조] {len(check_rows)}건")
            for item in check_rows:
                print(f"{item['date']} {item['ticker']} ours={item['ours']:,.0f}"
                      f" kiwoom={item['kiwoom']:,.0f} diff={item['diff']:+,.0f}")
        if not rows:
            print(f"{name}: 스냅샷 없음")
            continue
        last = rows[-1]
        cum = 1.0
        for r in twr(rows):
            cum *= 1 + r
        cum -= 1
        peak = float("-inf")
        mdd = 0.0
        for row in rows:
            peak = max(peak, row["equity"])
            if peak > 0:
                mdd = min(mdd, row["equity"] / peak - 1)
        print(f"{name} {last['date']} 평가 {last['equity']:,.0f}"
              f" (시드 {last['seed']:,.0f} · 확정 {last['realized_cum']:+,.0f}"
              f" · 미실현 {last['unrealized']:+,.0f})"
              f" 누적 TWR {cum:+.2%} · 일별 MDD {mdd:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
