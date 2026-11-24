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
US_DB = db_dir() / "us_ohlcv.duckdb"
US_V38_DB = db_dir() / "us_v38.sqlite3"

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
    fee: float = FEE,
    sell_tax: float = SELL_TAX,
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
    ext_capital = 0.0
    for i, day in enumerate(days):
        ext_today = False
        for fill in by_date.get(day, []):
            ticker = str(fill["ticker"])
            qty = fill["qty"]
            price = fill["price"]
            if qty is None or price is None or qty <= 0:
                continue
            external = bool(fill.get("external", False))
            f_fee = fill.get("fee", fee)
            if fill["side"] == "buy":
                buy_cost = qty * price * (1 + f_fee)
                cost[ticker] = cost.get(ticker, 0.0) + buy_cost
                held[ticker] = held.get(ticker, 0) + qty
                if external:
                    ext_capital += buy_cost
                    ext_today = True
            elif fill["side"] == "sell":
                have = held.get(ticker, 0)
                if have < qty:
                    raise ValueError(f"보유 부족 매도: {day} {ticker} 보유 {have} < 매도 {qty}")
                avg = cost.get(ticker, 0.0) / have if have else 0.0
                proceeds = qty * price * (1 - f_fee - sell_tax)
                realized.append((day, proceeds - avg * qty))
                cost[ticker] = cost.get(ticker, 0.0) - avg * qty
                held[ticker] = have - qty
                trades_cum += 1
                if external:
                    ext_capital -= proceeds
                    ext_today = True
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
        equity = capital + ext_capital + realized_cum + unrealized
        pnl = day_real + unrealized - prev_unreal
        if i == 0:
            flow: float = equity - pnl
            flow_note = "시작"
        else:
            flow = equity - prev_equity - pnl
            flow_note = ""
            for event in events:
                if event["date"] == day:
                    flow_note = event.get("note", "")
        if ext_today:
            flow_note = f"{flow_note} · 추가 자금" if flow_note else "추가 자금"
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
    """일별 시간가중 수익률. r_D = (equity − flow)/전날 equity − 1 (첫날 (equity−flow)/flow)."""
    out: list[float] = []
    prev: float | None = None
    for row in rows:
        if prev is None:
            flow = row["flow"]
            out.append((row["equity"] - flow) / flow if flow else 0.0)
        elif not prev:
            out.append(0.0)
        else:
            out.append((row["equity"] - row["flow"]) / prev - 1)
        prev = row["equity"]
    return out


def twr_mdd(rows: list[dict]) -> float:
    """TWR 지수 기준 MDD. idx_D = Π(1+r) (시작 1) 의 고점 대비 최저."""
    idx = 1.0
    peak = 1.0
    mdd = 0.0
    for r in twr(rows):
        idx *= 1 + r
        peak = max(peak, idx)
        if peak > 0:
            mdd = min(mdd, idx / peak - 1)
    return mdd


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

    sorted_days = sorted(days)
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
            if carried:
                nxt = None
                for d in sorted_days:
                    if d > day:
                        nxt = d
                        break
                if nxt is None:
                    continue  # 아직 보유 — 매도 미반영
                sell_day = nxt
            else:
                sell_day = day
            fills.append({"date": sell_day, "ticker": ticker, "side": "sell",
                          "qty": sq, "price": sp, "ref": ref})
    return fills


def _reset_events(wl_con: sqlite3.Connection, strategy: str,
                 first_date: str) -> list[dict]:
    """strategy_budget_resets(strategy) → SeedEvent + 리셋 전 시드."""
    first_date = str(first_date)
    resets: list[tuple[str, float | None, float, str]] = []
    if _table_exists(wl_con, "strategy_budget_resets"):
        try:
            db_rows = wl_con.execute(
                "SELECT old_capital, new_capital, since, note"
                " FROM strategy_budget_resets WHERE strategy=?"
                " ORDER BY date, rowid", (strategy,)).fetchall()
        except sqlite3.Error:
            db_rows = []
        for old, new, since, note in db_rows:
            new_f = _num(new)
            if since is None or new_f is None:
                continue
            resets.append((str(since), _num(old), new_f, note or ""))
    events = [{"date": since, "capital": new_f, "since": since, "note": note}
              for since, _old, new_f, note in resets]
    if resets and resets[0][1] is not None and resets[0][1] > 0 \
            and first_date < resets[0][0]:
        events.insert(0, {"date": first_date, "capital": resets[0][1],
                          "since": first_date, "note": "리셋 전 시드"})
    return events


def rebound_seed_events(wl_con: sqlite3.Connection, first_date: str) -> list[dict]:
    """strategy_budget_resets(rebound) + msig_settlements → SeedEvent 리스트."""
    events = _reset_events(wl_con, "rebound", first_date)
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


def _rights_tickers(wl_con: sqlite3.Connection) -> dict[str, str]:
    """acptno → ticker. rights_dip_positions 우선, 없으면 candidates."""
    tickers: dict[str, str] = {}
    for table in ("rights_dip_positions", "rights_dip_candidates"):
        if not _table_exists(wl_con, table):
            continue
        cols = _columns(wl_con, table)
        if "acptno" not in cols or "ticker" not in cols:
            continue
        try:
            db_rows = wl_con.execute(
                f"SELECT acptno, ticker FROM {table}").fetchall()
        except sqlite3.Error:
            continue
        for acptno, ticker in db_rows:
            if acptno is not None and ticker is not None:
                tickers.setdefault(str(acptno), str(ticker))
    return tickers


def _rights_acptno_fills(wl_con: sqlite3.Connection) -> list[tuple[str, dict]]:
    """(acptno, Fill) — rights_check 그룹핑용 내부 함수."""
    if not _table_exists(wl_con, "rights_dip_orders"):
        return []
    cols = _columns(wl_con, "rights_dip_orders")
    if "date" not in cols or "acptno" not in cols:
        return []
    wanted = ["id", "date", "acptno", "extra", "leg", "filled_qty", "filled_price"]
    avail = [c for c in wanted if c in cols]
    order = "date, id" if "id" in cols else "date, rowid"
    try:
        db_rows = wl_con.execute(
            f"SELECT {','.join(avail)} FROM rights_dip_orders ORDER BY {order}").fetchall()
    except sqlite3.Error:
        return []
    idx = {name: pos for pos, name in enumerate(avail)}

    def get(row, name: str):
        return row[idx[name]] if name in idx else None

    tickers = _rights_tickers(wl_con)
    out: list[tuple[str, dict]] = []
    for row in db_rows:
        raw_date, raw_acptno = get(row, "date"), get(row, "acptno")
        if raw_date is None or raw_acptno is None:
            continue
        ticker = tickers.get(str(raw_acptno))
        if ticker is None:
            continue
        fq = _qty(get(row, "filled_qty"))
        fp = _num(get(row, "filled_price"))
        if fq <= 0 or fp is None:
            continue
        leg = get(row, "leg")
        side = "sell" if leg in ("exit", "tp") else "buy"
        out.append((str(raw_acptno), {
            "date": str(raw_date), "ticker": ticker, "side": side,
            "qty": fq, "price": fp, "external": get(row, "extra") == 1,
            "ref": f"rights_dip:{raw_acptno}:{leg}:{get(row, 'id')}",
        }))
    return out


def rights_fills(wl_con: sqlite3.Connection, days: list[str]) -> list[dict]:
    """rights_dip_orders 체결 → Fill 리스트. days 는 시그니처 통일용(미사용)."""
    return [fill for _, fill in _rights_acptno_fills(wl_con)]


def rights_seed_events(wl_con: sqlite3.Connection, first_date: str) -> list[dict]:
    """strategy_budget_resets(rights_dip) → SeedEvent 리스트."""
    return _reset_events(wl_con, "rights_dip", first_date)


def rights_seed(config: dict, wl_con: sqlite3.Connection) -> float:
    """외부 시드 = config capital − rights_dip_capital 의 since 이후 capital 변화 합."""
    base = config.get("capital")
    if not _table_exists(wl_con, "rights_dip_capital"):
        return base
    cols = _columns(wl_con, "rights_dip_capital")
    if "capital" not in cols:
        return base
    since = config.get("capital_since")
    since_s = str(since) if since is not None else None
    wanted = ["date", "capital", "pnl"]
    avail = [c for c in wanted if c in cols]
    order = "id" if "id" in cols else "rowid"
    try:
        db_rows = wl_con.execute(
            f"SELECT {','.join(avail)} FROM rights_dip_capital ORDER BY {order}").fetchall()
    except sqlite3.Error:
        return base
    idx = {name: pos for pos, name in enumerate(avail)}

    def get(row, name: str):
        return row[idx[name]] if name in idx else None

    rows: list[tuple[float, float]] = []
    for row in db_rows:
        cap = _num(get(row, "capital"))
        if cap is None:
            continue
        raw_date = get(row, "date")
        if since_s is not None and (raw_date is None or str(raw_date) < since_s):
            continue
        pnl = _num(get(row, "pnl"))
        rows.append((cap, pnl if pnl is not None else 0.0))
    if not rows:
        return base
    prev = rows[0][0] - rows[0][1]
    total = 0.0
    for cap, _pnl in rows:
        total += cap - prev
        prev = cap
    return base - total


def rights_check(wl_con: sqlite3.Connection) -> list[dict]:
    """청산 완료 realized 건: acptno별 엔진 실현 vs 키움 pnl 대조."""
    if not _table_exists(wl_con, "rights_dip_positions"):
        return []
    cols = _columns(wl_con, "rights_dip_positions")
    need = {"acptno", "ticker", "close_date", "pnl", "pnl_source"}
    if not need <= cols:
        return []
    try:
        db_rows = wl_con.execute(
            "SELECT acptno, ticker, close_date, pnl FROM rights_dip_positions"
            " WHERE close_date IS NOT NULL AND pnl IS NOT NULL"
            " AND pnl_source='realized'").fetchall()
    except sqlite3.Error:
        return []
    by_acptno: dict[str, list[dict]] = {}
    for acptno, fill in _rights_acptno_fills(wl_con):
        by_acptno.setdefault(acptno, []).append(fill)
    out: list[dict] = []
    for acptno, ticker, close_date, pnl in db_rows:
        kiwoom = _num(pnl)
        if ticker is None or close_date is None or kiwoom is None:
            continue
        buy_qty = 0
        buy_cost = 0.0
        sell_qty = 0
        proceeds = 0.0
        for fill in by_acptno.get(str(acptno), []):
            if fill["side"] == "buy":
                buy_qty += fill["qty"]
                buy_cost += fill["qty"] * fill["price"] * (1 + FEE)
            else:
                sell_qty += fill["qty"]
                proceeds += fill["qty"] * fill["price"] * (1 - FEE - SELL_TAX)
        avg = buy_cost / buy_qty if buy_qty else 0.0
        ours = proceeds - avg * sell_qty
        out.append({"date": str(close_date), "ticker": str(ticker),
                    "ours": ours, "kiwoom": kiwoom, "diff": ours - kiwoom})
    return out


def msig_fills(wl_con: sqlite3.Connection, days: list[str]) -> list[dict]:
    """msig_orders 체결 → Fill 리스트. days 는 시그니처 통일용(미사용)."""
    if not _table_exists(wl_con, "msig_orders"):
        return []
    cols = _columns(wl_con, "msig_orders")
    if "date" not in cols or "ticker" not in cols:
        return []
    wanted = ["order_no", "date", "ticker", "side", "filled_qty", "avg_price"]
    avail = [c for c in wanted if c in cols]
    order = "date, order_no" if "order_no" in cols else "date, rowid"
    try:
        db_rows = wl_con.execute(
            f"SELECT {','.join(avail)} FROM msig_orders ORDER BY {order}").fetchall()
    except sqlite3.Error:
        return []
    idx = {name: pos for pos, name in enumerate(avail)}

    def get(row, name: str):
        return row[idx[name]] if name in idx else None

    fills: list[dict] = []
    for row in db_rows:
        raw_date, raw_ticker = get(row, "date"), get(row, "ticker")
        if raw_date is None or raw_ticker is None:
            continue
        side = get(row, "side")
        if side not in ("buy", "sell"):
            continue
        fq = _qty(get(row, "filled_qty"))
        fp = _num(get(row, "avg_price"))
        if fq <= 0 or fp is None or fp <= 0:
            continue
        fills.append({"date": str(raw_date), "ticker": str(raw_ticker), "side": side,
                      "qty": fq, "price": fp, "ref": f"msig:{get(row, 'order_no')}"})
    return fills


def msig_seed_events(wl_con: sqlite3.Connection, first_date: str) -> list[dict]:
    """msig_settlements 첫 행 prev_value → 시작 시드 + 행별 정산 이벤트."""
    first_date = str(first_date)
    if not _table_exists(wl_con, "msig_settlements"):
        return []
    cols = _columns(wl_con, "msig_settlements")
    if "date" not in cols:
        return []
    wanted = ["date", "prev_value", "msig_capital"]
    avail = [c for c in wanted if c in cols]
    try:
        db_rows = wl_con.execute(
            f"SELECT {','.join(avail)} FROM msig_settlements ORDER BY date").fetchall()
    except sqlite3.Error:
        return []
    idx = {name: pos for pos, name in enumerate(avail)}

    def get(row, name: str):
        return row[idx[name]] if name in idx else None

    events: list[dict] = []
    first_prev = _num(get(db_rows[0], "prev_value")) if db_rows else None
    if first_prev is not None:
        events.append({"date": first_date, "capital": first_prev,
                       "since": first_date, "note": "시작 시드"})
    for row in db_rows:
        raw_date = get(row, "date")
        cap = _num(get(row, "msig_capital"))
        if raw_date is None or cap is None:
            continue
        events.append({"date": str(raw_date), "capital": cap,
                       "since": first_date, "note": "msig 정산 이체"})
    events.sort(key=lambda e: e["date"])
    return events


def high52_fills(wl_con: sqlite3.Connection, days: list[str]) -> list[dict]:
    """high52_positions → Fill 리스트. days 는 시그니처 통일용(미사용)."""
    if not _table_exists(wl_con, "high52_positions"):
        return []
    cols = _columns(wl_con, "high52_positions")
    if "signal_date" not in cols or "ticker" not in cols:
        return []
    wanted = ["signal_date", "ticker", "buy_price", "buy_qty",
              "sell_status", "sell_date", "sell_price", "sell_qty"]
    avail = [c for c in wanted if c in cols]
    try:
        db_rows = wl_con.execute(
            f"SELECT {','.join(avail)} FROM high52_positions"
            " ORDER BY signal_date").fetchall()
    except sqlite3.Error:
        return []
    idx = {name: pos for pos, name in enumerate(avail)}

    def get(row, name: str):
        return row[idx[name]] if name in idx else None

    fills: list[dict] = []
    for row in db_rows:
        raw_date, raw_ticker = get(row, "signal_date"), get(row, "ticker")
        if raw_date is None or raw_ticker is None:
            continue
        day, ticker = str(raw_date), str(raw_ticker)
        ref = f"high52:{day}:{ticker}"
        bq = _qty(get(row, "buy_qty"))
        bp = _num(get(row, "buy_price"))
        if bq > 0 and bp is not None and bp > 0:
            fills.append({"date": day, "ticker": ticker, "side": "buy",
                          "qty": bq, "price": bp, "ref": ref})
        if get(row, "sell_status") == "filled":
            sq = _qty(get(row, "sell_qty"))
            sp = _num(get(row, "sell_price"))
            sell_date = get(row, "sell_date")
            if sq > 0 and sp is not None and sp > 0 and sell_date is not None:
                fills.append({"date": str(sell_date), "ticker": ticker, "side": "sell",
                              "qty": sq, "price": sp, "ref": ref})
    return fills


def high52_seed(config: dict, wl_con: sqlite3.Connection) -> float | None:
    """슬롯 수 × 슬롯 금액. 둘 중 하나라도 없으면 None."""
    slots = _num(config.get("slots"))
    amount = _num(config.get("slot_amount"))
    if slots is None or amount is None:
        return None
    return slots * amount


def high52_seed_events(wl_con: sqlite3.Connection, first_date: str) -> list[dict]:
    """high52 는 정산 이벤트 없음."""
    return []


def us_fills(wl_con: sqlite3.Connection, days: list[str]) -> list[dict]:
    """us_v38 orders 체결 + adopted 보유분 → Fill 리스트. wl_con·days 미사용."""
    fills: list[dict] = []
    if Path(US_V38_DB).exists():
        with connect_ro(US_V38_DB) as con:
            if _table_exists(con, "orders"):
                cols = _columns(con, "orders")
                if "date" in cols and "code" in cols:
                    wanted = ["order_no", "date", "code", "side",
                              "filled_qty", "avg_fill_price"]
                    avail = [c for c in wanted if c in cols]
                    order = "date, order_no" if "order_no" in cols else "date, rowid"
                    try:
                        db_rows = con.execute(
                            f"SELECT {','.join(avail)} FROM orders ORDER BY {order}"
                        ).fetchall()
                    except sqlite3.Error:
                        db_rows = []
                    idx = {name: pos for pos, name in enumerate(avail)}

                    def get(row, name: str):
                        return row[idx[name]] if name in idx else None

                    for row in db_rows:
                        raw_date, raw_code = get(row, "date"), get(row, "code")
                        if raw_date is None or raw_code is None:
                            continue
                        side = get(row, "side")
                        if side not in ("buy", "sell"):
                            continue
                        fq = _qty(get(row, "filled_qty"))
                        fp = _num(get(row, "avg_fill_price"))
                        if fq <= 0 or fp is None or fp <= 0:
                            continue
                        fills.append({"date": str(raw_date), "ticker": str(raw_code),
                                      "side": side, "qty": fq, "price": fp,
                                      "ref": f"us_v38:{get(row, 'order_no')}"})
    adopted_path = _resolve_config("research/private/us_oas/live/adopted.json")
    if adopted_path.exists():
        adopted = json.loads(adopted_path.read_text(encoding="utf-8")).get("adopted", [])
        for item in adopted:
            code, day = item.get("code"), item.get("date")
            qty = _qty(item.get("qty"))
            if code is None or day is None or qty <= 0:
                continue
            closes = load_closes(US_DB, [str(code)], str(day), str(day),
                                 ("ohlcv", "etf_ohlcv"))
            px = closes.get((str(day), str(code)))
            if px is None:
                print(f"us_v38: adopted 종가 없음 — 건너뜀 {code} {day}")
                continue
            fills.append({"date": str(day), "ticker": str(code), "side": "buy",
                          "qty": qty, "price": px, "external": True, "fee": 0.0,
                          "ref": f"us_v38:adopted:{code}"})
    return fills


def us_seed(config: dict, wl_con: sqlite3.Connection) -> float | None:
    """max_equity_usd (미화 시드). 없으면 None."""
    return _num(config.get("max_equity_usd"))


def us_seed_events(wl_con: sqlite3.Connection, first_date: str) -> list[dict]:
    """us_v38 은 정산 이벤트 없음."""
    return []


def log_seed(perf_db: str | Path, strategy: str, date: str, capital, since: str) -> bool:
    """config 시드가 전략 마지막 기록과 (capital, since) 쌍으로 다르면 남기고 True."""
    ensure(perf_db)
    new_since = str(since) if since is not None else None
    with connect_rw(perf_db) as con:
        last = con.execute(
            "SELECT capital, since FROM strategy_seed WHERE strategy=? ORDER BY date DESC LIMIT 1",
            (strategy,)).fetchone()
        if last is not None and last[0] == capital and last[1] == new_since:
            return False
        con.execute(
            "INSERT OR REPLACE INTO strategy_seed(strategy, date, capital, since, note)"
            " VALUES (?,?,?,?,?)",
            (strategy, str(date), capital, new_since, "config"))
        return True


def load_config_seed_events(perf_db: str | Path, strategy: str,
                            default_since: str | None = None) -> list[dict]:
    """strategy_seed 행 → SeedEvent 리스트 (note 'config').
    DB since NULL 이면 default_since(없으면 행 날짜)."""
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
        if since is not None:
            since_s = str(since)
        elif default_since is not None:
            since_s = str(default_since)
        else:
            since_s = day
        events.append({"date": day, "capital": cap,
                       "since": since_s, "note": "config"})
    return events


def merge_seed_events(adapter_events: list[dict], config_events: list[dict]) -> list[dict]:
    """어댑터 + config 날짜순 병합. 같은 date 면 어댑터 우선, config 는 유효 상태와
    (capital, since) 쌍이 같을 때만 버림."""
    adapter_dates = {e["date"] for e in adapter_events}
    combined = [(e["date"], 0, e) for e in adapter_events] \
        + [(e["date"], 1, e) for e in config_events]
    combined.sort(key=lambda t: (t[0], t[1]))
    merged: list[dict] = []
    current: tuple | None = None
    for date, prio, event in combined:
        if prio == 0:
            merged.append(event)
            current = (event["capital"], event["since"])
        else:
            if date in adapter_dates:
                continue
            if current is not None and current[0] == event["capital"] \
                    and current[1] == event["since"]:
                continue
            merged.append(event)
            current = (event["capital"], event["since"])
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


def load_days(db: str | Path, start: str, end: str,
              table: str = "ohlcv") -> list[str]:
    """거래일 오름차순 (기본 ohlcv 테이블)."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        db_rows = con.execute(
            f"SELECT DISTINCT date FROM {table} WHERE date BETWEEN ? AND ? ORDER BY date",
            [start, end]).fetchall()
    finally:
        con.close()
    return [row[0] for row in db_rows]


def load_closes(db: str | Path, tickers: list[str],
               start: str, end: str,
               tables: tuple[str, ...] = ("ohlcv",)) -> dict[tuple[str, str], float]:
    """{(date, ticker): close}. tables 순서대로 합침(먼저 나온 값 우선)."""
    if not tickers:
        return {}
    placeholders = ",".join("?" for _ in tickers)
    out: dict[tuple[str, str], float] = {}
    con = duckdb.connect(str(db), read_only=True)
    try:
        for table in tables:
            db_rows = con.execute(
                f"SELECT date, ticker, close FROM {table}"
                f" WHERE date BETWEEN ? AND ? AND ticker IN ({placeholders})",
                [start, end, *tickers]).fetchall()
            for row in db_rows:
                out.setdefault((row[0], row[1]), row[2])
    finally:
        con.close()
    return out


ADAPTERS = {
    "rebound": {"fills": rebound_fills, "events": rebound_seed_events,
                "seed": None, "check": realized_check},
    "rights_dip": {"fills": rights_fills, "events": rights_seed_events,
                   "seed": rights_seed, "check": rights_check},
    "msig": {"fills": msig_fills, "events": msig_seed_events,
             "seed": None, "check": None},
    "high52": {"fills": high52_fills, "events": high52_seed_events,
               "seed": high52_seed, "check": None,
               "config": "research/private/high52_quiet_breakout/live/config.json"},
    "us_v38": {"fills": us_fills, "events": us_seed_events,
               "seed": us_seed, "check": None,
               "config": "research/private/us_oas/live/config.json",
               "market": "us", "fee": 0.001, "sell_tax": 0.0},
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="전략 일별 스냅샷 (실매매 live)")
    parser.add_argument("--strategy", choices=sorted(ADAPTERS), default=None)
    parser.add_argument("--end", default=None, help="YYYYMMDD (기본 오늘 KST)")
    parser.add_argument("--check", action="store_true", help="realized 대조 표 출력")
    parser.add_argument("--dry-run", action="store_true", help="저장 안 함")
    args = parser.parse_args(argv)
    obs_date = now_seoul().strftime("%Y%m%d")
    end = args.end or obs_date
    if not args.dry_run:
        ensure(PERF_DB)
    names = [args.strategy] if args.strategy else sorted(ADAPTERS)
    for name in names:
        adapter = ADAPTERS[name]
        use_us = adapter.get("market") == "us"
        fills_fn, events_fn = adapter["fills"], adapter["events"]
        seed_fn, check_fn = adapter.get("seed"), adapter.get("check")
        config = json.loads(_resolve_config(
            adapter.get("config") or STRATEGIES[name][1]).read_text(encoding="utf-8"))
        base_capital = config.get("capital")
        if seed_fn is None and base_capital is None:
            print(f"{name}: config capital 없음 — 건너뜀")
            continue
        capital_since = config.get("capital_since")
        if capital_since == "":
            capital_since = None
        with connect_ro(WL_DB) as wl_con:
            capital = seed_fn(config, wl_con) if seed_fn is not None else base_capital
            if capital is None:
                print(f"{name}: 시드 없음 — 건너뜀")
                continue
            logged = False
            if not args.dry_run:
                logged = log_seed(PERF_DB, name, obs_date, capital, capital_since)
            probe = fills_fn(wl_con, [])
            if not probe:
                print(f"{name}: 체결 없음")
                continue
            first = min(fill["date"] for fill in probe)
            if use_us:
                days = load_days(US_DB, first, end, "ohlcv")
            else:
                days = load_days(KRX_DB, first, end)
            if not days:
                print(f"{name}: 거래일 없음 ({first}~{end})")
                continue
            fills = fills_fn(wl_con, days)
            events = events_fn(wl_con, first)
            check_rows = check_fn(wl_con) if args.check and check_fn is not None else None
        tickers = sorted({fill["ticker"] for fill in fills})
        if use_us:
            closes = load_closes(US_DB, tickers, first, end, ("ohlcv", "etf_ohlcv"))
        else:
            closes = load_closes(KRX_DB, tickers, first, end)
        stored = load_config_seed_events(PERF_DB, name, default_since=first)
        obs_cap = _num(capital)
        if obs_cap is None:
            config_events = stored
        elif (not logged and capital_since is None and stored
                and stored[-1]["capital"] == obs_cap):
            config_events = stored
        else:
            obs_since = str(capital_since) if capital_since is not None else first
            pre_existing = [e for e in stored if e["date"] != obs_date] if logged else stored
            if obs_date > end and not events and not pre_existing:
                mem_date = days[0]
                mem_since = str(capital_since) if capital_since is not None else first
            else:
                mem_date, mem_since = obs_date, obs_since
            config_events = stored + [{"date": mem_date, "capital": obs_cap,
                                        "since": mem_since, "note": "config"}]
        merged = merge_seed_events(events, config_events)
        rows = compute(fills, merged, closes, days,
                       fee=adapter.get("fee", FEE),
                       sell_tax=adapter.get("sell_tax", SELL_TAX))
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
        mdd = twr_mdd(rows)
        print(f"{name} {last['date']} 평가 {last['equity']:,.0f}"
              f" (시드 {last['seed']:,.0f} · 확정 {last['realized_cum']:+,.0f}"
              f" · 미실현 {last['unrealized']:+,.0f})"
              f" 누적 TWR {cum:+.2%} · 일별 MDD {mdd:.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
