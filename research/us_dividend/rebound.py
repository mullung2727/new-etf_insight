"""미국 안정배당주 급락 후 반등 단기 매매 (PLAN_US_DIVIDEND_REBOUND 1·2·3·4절)."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from research.backtest_daily.perf import cagr, summary, yearly
from research.backtest_daily.stats import day_median, day_weighted_mean, day_win_rate
from research.backtest_daily.validate import placebo_percentile
from research.us_dividend.run import (
    FACTS_DB,
    US_OHLCV_DB,
    _cik_str,
    _dps_asof_filed,
    _load_companies,
    _load_splits,
    _tkey,
    _trading_dates,
    excluded_sic,
    fresh_consecutive,
    load_dps,
    split_factor,
    stable,
)

X_LIST = [0.30, 0.40, 0.50, 0.60, 0.70]
TP_LIST = [("+15%", "fixed", 0.15), ("+25%", "fixed", 0.25),
           ("+40%", "fixed", 0.40), ("되돌림1/3", "retr", 1 / 3),
           ("되돌림1/2", "retr", 1 / 2)]
MAX_HOLD = 60
COST = 0.0035
DIV_TAX = 0.15
LOOKBACK = 252
P1_DRAWS = 200
START = "20160101"
COVID_OUT = ("20200201", "20200430")
RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "REBOUND.md"
SENS_X_LIST = [0.60, 0.70, 0.80]
SENS_HOLD_LIST = [20, 60, 120]
HOLD_RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "REBOUND_HOLD.md"
P1_NOTE = "되돌림 목표는 무작위 종목의 고점-가격 차이가 작아 목표가 가까워지는 구조라 대조 무효, 제외"
FOCUS_X = 0.70
FOCUS_HOLD_LIST = [20, 60, 120]
FOCUS_TP_LIST = [("만료만", "none", 0), ("+15%", "fixed", 0.15),
                 ("+25%", "fixed", 0.25), ("+40%", "fixed", 0.40),
                 ("+60%", "fixed", 0.60), ("+100%", "fixed", 1.00)]
FOCUS_COVID_OUT = ("20200201", "20200630")
FOCUS_RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "REBOUND_FOCUS70.md"
REGIME_RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "REBOUND_REGIME.md"
REGIME_PAYER_RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "REBOUND_REGIME_PAYER.md"
CUT_RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "DIV_CUT.md"
CUTREB_RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "REBOUND_CUT.md"
UNIV_RESULTS_PATH = Path(__file__).resolve().parents[1] / "private" / "us_dividend" / "REBOUND_UNIV.md"
CUT_BUCKETS = ["0~-20", "-20~-40", "-40~-60", "-60+"]
CUT_LO, CUT_HI = "201601", "202510"


def fixed_target(entry: float, pct: float) -> float:
    """고정형 목표가."""
    return float(entry) * (1.0 + float(pct))


def retr_target(entry: float, hi: float, frac: float) -> float:
    """되돌림형 목표가."""
    return float(entry) + (float(hi) - float(entry)) * float(frac)


def _rolling_hi(close: np.ndarray, lookback: int) -> np.ndarray:
    """i-lookback+1..i 종가 최대 (i<lookback-1·구간 NaN이면 NaN)."""
    s = pd.Series(np.asarray(close, dtype=float))
    return s.rolling(int(lookback), min_periods=int(lookback)).max().to_numpy()


def tp_exit(open_, high, close, e: int, tp: float, max_hold: int = 60):
    """진입 e·목표 tp 청산 → (exit_i, px, reason) | None(미청산)."""
    o = np.asarray(open_, dtype=float)
    h = np.asarray(high, dtype=float)
    c = np.asarray(close, dtype=float)
    e = int(e)
    k_end = e + int(max_hold) - 1
    if e < 0 or k_end >= len(c):
        return None
    for k in range(e + 1, k_end + 1):
        if np.isfinite(o[k]) and o[k] >= tp:
            return (k, float(o[k]), "tp_gap")
        if np.isfinite(h[k]) and h[k] >= tp:
            return (k, float(tp), "tp")
    finite = np.flatnonzero(np.isfinite(c[e:k_end + 1]))
    if finite.size == 0:
        return None
    k = e + int(finite[-1])
    return (k, float(c[k]), "time")


def simulate(open_, high, low, close, eligible, x: float | np.ndarray,
             tp_kind: str, tp_val: float, max_hold: int = 60,
             lookback: int = 252) -> list[dict]:
    """한 종목·한 X(스칼라·길이 n 배열)·한 목표 시뮬레이션 (미청산은 reason "unclosed"로 포함)."""
    o = np.asarray(open_, dtype=float)
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    elig = np.asarray(eligible, dtype=bool)
    n = len(c)
    hi = _rolling_hi(c, lookback)
    xv = np.broadcast_to(np.asarray(x, dtype=float), c.shape)
    th = hi * (1 - xv)
    out: list[dict] = []
    armed = True
    holding_until = -1
    i = 1
    while i <= n - 2:
        if i <= holding_until:
            i += 1
            continue
        t, p = th[i], hi[i - 1] * (1 - xv[i])
        finite = (np.isfinite(t) and np.isfinite(p)
                  and np.isfinite(c[i]) and np.isfinite(c[i - 1]))
        fire = (finite and armed and bool(elig[i])
                and c[i] <= t and c[i - 1] > p)
        if fire:
            armed = False
            e = i + 1
            entry = o[e]
            if np.isfinite(entry) and entry > 0:
                if tp_kind == "retr":
                    tp = retr_target(entry, hi[i], tp_val)
                elif tp_kind == "fixed":
                    tp = fixed_target(entry, tp_val)
                elif tp_kind == "none":
                    tp = float("inf")
                else:
                    raise ValueError(f"unknown tp_kind: {tp_kind}")
                res = tp_exit(o, h, c, e, tp, max_hold)
                if res is None:
                    out.append({"sig_i": i, "entry_i": e, "exit_i": None,
                                "entry": float(entry), "exit_px": None,
                                "reason": "unclosed", "ret": float("nan"),
                                "hold": None, "mae": float("nan")})
                    break
                k, px, reason = res
                w = lo[e:k + 1]
                w = w[np.isfinite(w)]
                mae = float(w.min() / entry - 1) if w.size else float("nan")
                out.append({"sig_i": i, "entry_i": e, "exit_i": k,
                            "entry": float(entry), "exit_px": float(px),
                            "reason": reason,
                            "ret": float(px / entry - 1 - COST),
                            "hold": k - e + 1, "mae": mae})
                holding_until = k
                i = k + 1
                continue
            i += 1
            continue
        if finite:
            armed = bool(c[i] > t)
        i += 1
    return out


def market_regime(spy_close: np.ndarray, kind: str) -> np.ndarray:
    """SPY 급락기 마스크. hi252: 당일 ≤252일 고점×0.9. fast20: 당일 ≤20일 고점×0.9 판정 후 60일간 유지."""
    c = np.asarray(spy_close, dtype=float)
    n = len(c)
    if kind == "hi252":
        w = 252
    elif kind == "fast20":
        w = 20
    else:
        raise ValueError(f"unknown kind: {kind}")
    roll = pd.Series(c).rolling(w, min_periods=1).max().to_numpy()
    trig = np.isfinite(c) & np.isfinite(roll) & (c <= roll * 0.9)
    if kind == "hi252":
        return trig
    out = np.zeros(n, dtype=bool)
    for i in np.flatnonzero(trig):
        out[i:min(n, i + 60)] = True
    return out


def div_array(axis, dates, amounts) -> np.ndarray:
    """배당락일→축 인덱스(휴장일은 다음 거래일) 매핑 배당 배열."""
    ax = list(axis)
    out = np.zeros(len(ax), dtype=float)
    if not ax:
        return out
    am = list(amounts)
    for k, dt in enumerate(list(dates)):
        a = float(am[k])
        if not np.isfinite(a):
            continue
        s = str(dt)
        if s < ax[0] or s > ax[-1]:
            continue
        out[int(np.searchsorted(ax, s, side="left"))] += a
    return out


def trade_div(div, e: int, k: int) -> float:
    """보유 구간(e < d <= k) 배당 합."""
    v = np.asarray(div, dtype=float)
    lo = max(int(e) + 1, 0)
    hi = min(int(k), len(v) - 1)
    if hi < lo:
        return 0.0
    return float(np.nansum(v[lo:hi + 1]))


def month_end_idx(axis, lo: str, hi: str) -> list[int]:
    """월별 마지막 거래일 인덱스 (그달 YYYYMM이 lo~hi, 오름차순)."""
    ax = [str(d) for d in axis]
    out: list[int] = []
    k, n = 0, len(ax)
    while k < n:
        ym = ax[k][:6]
        j = k
        while j + 1 < n and ax[j + 1][:6] == ym:
            j += 1
        if lo <= ym <= hi:
            out.append(j)
        k = j + 1
    return out


def window_sum(div, i: int, w: int = 252) -> tuple[float, float]:
    """직전 합 div[i-w+1..i], 다음 합 div[i+1..i+w] (창이 범위 밖이면 NaN)."""
    v = np.asarray(div, dtype=float)
    n = len(v)
    i, w = int(i), int(w)
    prev = float(np.nansum(v[i - w + 1:i + 1])) if i - w + 1 >= 0 else float("nan")
    nxt = float(np.nansum(v[i + 1:i + w + 1])) if i + w < n else float("nan")
    return (prev, nxt)


def div_baseline(div, i: int, k: int = 4) -> tuple[float, float, int] | None:
    """신호일 이전 배당 기준 → (마지막 금액, 최근 간격 중앙값, 마지막 위치) | None."""
    v = np.asarray(div, dtype=float)
    i = int(i)
    if i >= len(v):
        i = len(v) - 1
    if i < 0 or len(v) == 0:
        return None
    idx = np.flatnonzero(np.isfinite(v[:i + 1]) & (v[:i + 1] != 0))
    if idx.size < 2:
        return None
    recent = idx[-max(int(k), 2):]
    gaps = np.diff(recent).astype(float)
    return (float(v[idx[-1]]), float(np.median(gaps)), int(idx[-1]))


def detect_cut(div, sig_i: int, entry_i: int, exit_i: int | None) -> tuple[int, str] | None:
    """보유 중 배당 삭감·중단 감지 → (감지일, "cut"/"omit") | None."""
    base = div_baseline(div, sig_i)
    if base is None:
        return None
    if exit_i is None:
        return None
    amt, gap, last = base
    v = np.asarray(div, dtype=float)
    lo = max(int(entry_i) + 1, 0)
    hi = min(int(exit_i), len(v) - 1)
    for d in range(lo, hi + 1):
        dv = float(v[d]) if np.isfinite(v[d]) else 0.0
        if dv > 0:
            if dv < amt * 0.9:
                return (d, "cut")
            amt, last = dv, d
        elif d - last > gap * 1.5:
            return (d, "omit")
    return None


def sell_on_detect(trade: dict, open_, det_i: int | None) -> dict:
    """감지일 다음 시가 청산 사본 (조건 안 맞으면 원본 사본)."""
    out = dict(trade)
    if det_i is None:
        return out
    try:
        d = int(det_i)
    except (TypeError, ValueError):
        return out
    ex = out.get("exit_i")
    if ex is None:
        return out
    nx = d + 1
    if nx > int(ex):
        return out
    o = np.asarray(open_, dtype=float)
    if nx < 0 or nx >= len(o):
        return out
    px = float(o[nx])
    if not (np.isfinite(px) and px > 0):
        return out
    try:
        entry = float(out.get("entry"))
    except (TypeError, ValueError):
        return out
    if not (np.isfinite(entry) and entry > 0):
        return out
    out["exit_i"] = nx
    out["exit_px"] = px
    out["reason"] = "cut_sell"
    out["ret"] = float(px / entry - 1 - COST)
    try:
        out["hold"] = nx - int(out.get("entry_i")) + 1
    except (TypeError, ValueError):
        pass
    return out


def special_suspect(div, i: int, w: int = 252) -> bool:
    """직전 창 0 아닌 배당 3회 이상이고 최대 > 중앙값 × 2."""
    v = np.asarray(div, dtype=float)
    i, w = int(i), int(w)
    win = v[max(0, i - w + 1):i + 1]
    nz = win[np.isfinite(win) & (win != 0)]
    if nz.size < 3:
        return False
    return bool(nz.max() > float(np.median(nz)) * 2)


def dd_bucket4(dd) -> str | None:
    """낙폭 4구간. NaN → None."""
    try:
        v = float(dd)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    if v > -0.2:
        return "0~-20"
    if v > -0.4:
        return "-20~-40"
    if v > -0.6:
        return "-40~-60"
    return "-60+"


def div_group(stable_flag: bool, prev_sum: float) -> str:
    """신호일 배당 그룹: 안정배당 / 불안정배당 / 무배당."""
    if bool(stable_flag):
        return "안정배당"
    try:
        v = float(prev_sum)
    except (TypeError, ValueError):
        return "무배당"
    if np.isfinite(v) and v > 0:
        return "불안정배당"
    return "무배당"


def payer_mask(stable, div, w: int = 252) -> np.ndarray[bool]:
    """배당 지급사 마스크: 안정 또는 직전 w 거래일(당일 포함) 배당 합 > 0."""
    st = np.asarray(stable, dtype=bool)
    v = np.asarray(div, dtype=float)
    v = np.where(np.isfinite(v), v, 0.0)
    s = pd.Series(v).rolling(int(w), min_periods=1).sum().to_numpy()
    return st | (s > 0)


def run_slots(trades: list[dict], close_by_ticker: dict, n_days: int,
              slots: int = 10, fee: float = 0.00175,
              div_by_ticker: dict | None = None,
              div_tax: float = DIV_TAX) -> dict:
    """칸 계좌 시뮬레이션 (청산분만 입력) → equity·invested·taken·skipped·taken_trades."""
    n = int(n_days)
    by_entry: dict[int, list[dict]] = {}
    for t in trades:
        by_entry.setdefault(int(t["entry_i"]), []).append(t)
    equity = np.zeros(n, dtype=float)
    invested = np.zeros(n, dtype=float)
    cash = 1.0
    holds: list[dict] = []
    taken = 0
    skipped = 0
    taken_trades: list[dict] = []
    for d in range(n):
        if div_by_ticker is not None:
            for h in holds:
                if h["entry_i"] < d <= h["exit_i"]:
                    arr = div_by_ticker.get(h["ticker"])
                    if arr is not None and d < len(arr):
                        dv = float(arr[d])
                        if np.isfinite(dv) and dv:
                            cash += dv * h["shares"] * (1 - div_tax)
        for h in [x for x in holds if x["exit_i"] == d]:
            cash += h["shares"] * h["exit_px"] * (1 - fee)
            holds.remove(h)
        eq_prev = 1.0 if d == 0 else equity[d - 1]
        for t in sorted(by_entry.get(d, []), key=lambda z: z["depth"]):
            if len(holds) >= slots or t["ticker"] in {x["ticker"] for x in holds}:
                skipped += 1
                continue
            entry = float(t["entry"])
            alloc = min(cash, eq_prev / slots)
            if not (np.isfinite(entry) and entry > 0) or alloc <= 0:
                skipped += 1
                continue
            holds.append({"ticker": t["ticker"],
                          "entry_i": int(t["entry_i"]),
                          "shares": alloc * (1 - fee) / entry,
                          "exit_i": int(t["exit_i"]),
                          "exit_px": float(t["exit_px"]), "last": entry})
            cash -= alloc
            taken += 1
            taken_trades.append(t)
        hv = 0.0
        for h in holds:
            cc = close_by_ticker[h["ticker"]]
            px = cc[d] if d < len(cc) else np.nan
            if np.isfinite(px):
                h["last"] = float(px)
            hv += h["shares"] * h["last"]
        equity[d] = cash + hv
        invested[d] = hv / equity[d] if equity[d] > 0 else 0.0
    return {"equity": equity, "invested": invested, "taken": taken,
            "skipped": skipped, "taken_trades": taken_trades}


def div_ok_mask(dates, rows: pd.DataFrame, splits) -> np.ndarray:
    """회사 1곳 DPS 구간별 안정 판정 → 날짜 축 길이 bool 배열."""
    d = pd.to_datetime(pd.Series(list(dates)), format="%Y%m%d", errors="coerce")
    out = np.zeros(len(d), dtype=bool)
    if rows is None or len(rows) == 0:
        return out
    r = rows.copy()
    r["_filed"] = pd.to_datetime(r["filed"], errors="coerce")
    r["_end"] = pd.to_datetime(r["end"], errors="coerce")
    r = r[r["_filed"].notna() & r["_end"].notna()]
    if r.empty:
        return out
    fdates = sorted(r["_filed"].dt.normalize().unique())
    for k, f in enumerate(fdates):
        m = d > f
        if k + 1 < len(fdates):
            m = m & (d <= fdates[k + 1])
        if not bool(m.any()):
            continue
        asof = _dps_asof_filed(r, f + pd.Timedelta(days=1))
        if asof.empty:
            continue
        g = asof.sort_values("end").tail(3)
        if len(g) != 3:
            continue
        ends = list(g["end"])
        if not fresh_consecutive(ends, ends[-1]):
            continue
        dps3 = [float(v) / split_factor(splits, fl)
                for v, fl in zip(g["val"], g["filed"])]
        if not stable(dps3):
            continue
        ok_t = m.to_numpy() & (d <= ends[-1] + pd.Timedelta(days=550)).to_numpy()
        out[ok_t] = True
    return out


def _load_ohlcv(tickers: list[str], db=US_OHLCV_DB) -> pd.DataFrame:
    """대상 티커 일봉 long."""
    cols = ["date", "ticker", "open", "high", "low", "close", "trading_value"]
    if not tickers:
        return pd.DataFrame(columns=cols)
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute(
            "SELECT date,ticker,open,high,low,close,trading_value FROM ohlcv"
            f" WHERE ticker IN ({','.join('?' * len(tickers))})",
            list(tickers)).fetchdf()
    finally:
        con.close()
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["date"] = df["date"].astype(str)
    df["ticker"] = df["ticker"].astype(str)
    return df.drop_duplicates(["date", "ticker"], keep="last")


def _load_spy(db=US_OHLCV_DB) -> dict[str, float]:
    """날짜 → SPY 종가."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute(
            "SELECT date, close FROM etf_ohlcv WHERE ticker = 'SPY'").fetchdf()
    finally:
        con.close()
    if df.empty:
        return {}
    df["date"] = df["date"].astype(str)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).drop_duplicates("date", keep="last")
    return dict(zip(df["date"], df["close"].astype(float)))


def _load_spy_div(db=US_OHLCV_DB) -> dict[str, float]:
    """날짜 → SPY 주당 배당 (etf_dividends, 없으면 {})."""
    try:
        con = duckdb.connect(str(db), read_only=True)
        try:
            df = con.execute(
                "SELECT date, amount FROM etf_dividends"
                " WHERE ticker = 'SPY'").fetchdf()
        finally:
            con.close()
    except Exception:
        return {}
    if df.empty:
        return {}
    df["date"] = df["date"].astype(str)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["amount"])
    out: dict[str, float] = {}
    for d, v in zip(df["date"], df["amount"]):
        out[str(d)] = out.get(str(d), 0.0) + float(v)
    return out


def _cell_df(trades: list[dict], axis: list[str], spy: dict[str, float],
             drop_lo: str | None = None, drop_hi: str | None = None) -> pd.DataFrame:
    """청산분만 → date(진입일)·ret·hit·hold·mae·ex."""
    cols = ["date", "ret", "hit", "hold", "mae", "ex"]
    rows = []
    for t in trades:
        if t["reason"] == "unclosed":
            continue
        ed = axis[t["entry_i"]]
        if drop_lo is not None and drop_lo <= ed <= drop_hi:
            continue
        sp0 = spy.get(axis[t["entry_i"] - 1])
        sp1 = spy.get(axis[t["exit_i"]])
        if sp0 and sp1 and sp0 > 0:
            ex = t["ret"] - (sp1 / sp0 - 1)
        else:
            ex = float("nan")
        rows.append({"date": ed, "ret": t["ret"],
                     "hit": 1.0 if t["reason"] in ("tp", "tp_gap") else 0.0,
                     "hold": t["hold"], "mae": t["mae"], "ex": ex})
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols)


def _fmt(x, digits: int = 2) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(v):
        return "-"
    return f"{v:.{digits}f}"


def _pct(x, digits: int = 1) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(v):
        return "-"
    return f"{v * 100:.{digits}f}%"


def _med(values) -> float:
    """유한값 전체 중앙값 (없으면 NaN)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    return float(np.median(v)) if v.size else float("nan")


def _cell_row(df: pd.DataFrame, n_unclosed: int) -> list[str]:
    """칸 표 한 행 셀."""
    n = len(df)
    ndays = int(df["date"].nunique()) if n else 0
    mae = pd.to_numeric(df["mae"], errors="coerce").to_numpy(dtype=float) if n else np.array([])
    mae = mae[np.isfinite(mae)]
    return [str(n), str(ndays), str(n_unclosed),
            _pct(day_weighted_mean(df, "hit")),
            _pct(day_weighted_mean(df, "ret")),
            _pct(day_median(df, "ret")),
            _pct(day_win_rate(df, "ret")),
            _fmt(df["hold"].mean()) if n else "-",
            _pct(np.median(mae)) if mae.size else "-",
            _pct(np.quantile(mae, 0.10)) if mae.size else "-",
            _pct(day_weighted_mean(df, "ex"))]


def drop_entry_window(trades: list[dict], axis: list[str],
                      lo: str, hi: str) -> list[dict]:
    """진입일 lo~hi(포함) 건 제외."""
    return [t for t in trades if not (lo <= axis[t["entry_i"]] <= hi)]


def first_entry_per_ticker(trades: list[dict]) -> list[dict]:
    """ticker별 가장 이른 entry_i 1건만."""
    best: dict[str, dict] = {}
    for t in trades:
        k = t["ticker"]
        if k not in best or t["entry_i"] < best[k]["entry_i"]:
            best[k] = t
    return list(best.values())


def _placebo(entry_idx: list[int], actual: float, pools: dict[int, list[str]],
             data: dict, tp_kind: str, tp_val: float,
             axis: list[str], draws: int = P1_DRAWS,
             max_hold: int = MAX_HOLD) -> tuple[float, float]:
    """P1 무작위 대조 → (draws 평균, 실제 백분위)."""
    outs: list[float] = []
    for seed in range(int(draws)):
        rng = np.random.default_rng(seed)
        rows = []
        for j in entry_idx:
            pool = pools.get(j)
            if not pool:
                continue
            a = data[pool[int(rng.integers(len(pool)))]]
            entry = a["open"][j]
            if not (np.isfinite(entry) and entry > 0):
                continue
            if tp_kind == "retr":
                hj = a["hi"][j - 1]
                if not np.isfinite(hj):
                    continue
                tp = retr_target(entry, hj, tp_val)
            elif tp_kind == "none":
                tp = float("inf")
            else:
                tp = fixed_target(entry, tp_val)
            res = tp_exit(a["open"], a["high"], a["close"], j, tp, max_hold)
            if res is None:
                continue
            _, px, _ = res
            rows.append({"date": axis[j], "ret": float(px / entry - 1 - COST)})
        df = pd.DataFrame(rows, columns=["date", "ret"])
        outs.append(day_weighted_mean(df, "ret"))
    return placebo_percentile(actual, outs)


def _placebo_slots(taken_trades, pools, data, axis, n_days, s,
                 draws: int = 200,
                 div_by_ticker: dict | None = None,
                 div_tax: float = DIV_TAX) -> list[float]:
    """체결 진입일·건수 그대로 무작위 종목 계좌 → START 이후 연 복리 draws."""
    close_by = {tk: a["close"] for tk, a in data.items()}
    outs: list[float] = []
    for seed in range(int(draws)):
        rng = np.random.default_rng(seed)
        ptr: list[dict] = []
        for t in taken_trades:
            j = int(t["entry_i"])
            pool = pools.get(j)
            if not pool:
                continue
            pick = pool[int(rng.integers(len(pool)))]
            a = data[pick]
            entry = a["open"][j]
            if not (np.isfinite(entry) and entry > 0):
                continue
            res = tp_exit(a["open"], a["high"], a["close"], j,
                          fixed_target(entry, 0.25), 120)
            if res is None:
                continue
            k, px, _ = res
            ptr.append({"ticker": pick, "entry_i": j, "exit_i": k,
                        "entry": float(entry), "exit_px": float(px), "depth": 0})
        eq = run_slots(ptr, close_by, int(n_days),
                       div_by_ticker=div_by_ticker,
                       div_tax=div_tax)["equity"]
        outs.append(cagr(pd.Series(eq[s:] / eq[s - 1:-1] - 1)))
    return outs


def _load_axis() -> list[str]:
    """START 앞 LOOKBACK 포함 거래일 축."""
    dates_all = _trading_dates()
    idx0 = next(i for i, d in enumerate(dates_all) if d >= START)
    axis = dates_all[max(0, idx0 - LOOKBACK):]
    print(f"[info] axis {axis[0]}~{axis[-1]} ({len(axis)}일)", flush=True)
    return axis


def _drop_candidates(threshold: float = 0.3, since: str = "20150101",
                     db=US_OHLCV_DB) -> set[str]:
    """since 이후 종가 ≤ threshold × 252행 최고 종가인 날이 한 번이라도 있는 티커 집합."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        df = con.execute(
            "SELECT DISTINCT ticker FROM ("
            " SELECT ticker,"
            " close <= ? * MAX(close) OVER (PARTITION BY ticker ORDER BY date"
            " ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS hit"
            " FROM ohlcv WHERE date >= ?)"
            " WHERE hit", [float(threshold), str(since)]).fetchdf()
    finally:
        con.close()
    if df.empty:
        return set()
    return {str(t) for t in df["ticker"].unique()}


def _load_data(axis: list[str], all_stocks: bool = False,
               drop_threshold: float = 0.3) -> tuple[dict, dict]:
    """배당 안정·유동성 eligible → (data, spy). all_stocks면 유동성만 + stable 별도."""
    companies = _load_companies()
    companies = companies[~companies["sic"].map(excluded_sic)].reset_index(drop=True)
    print(f"[info] companies {len(companies)}", flush=True)
    dps = load_dps()
    dps["cik"] = _cik_str(dps["cik"])
    splits = _load_splits()
    div_ok: dict[str, np.ndarray] = {}
    rows = companies.itertuples()
    if all_stocks:
        drop = {_tkey(t) for t in _drop_candidates(threshold=drop_threshold)}
        keep = companies[companies["ticker"].map(_tkey).isin(drop)]
        print(f"[info] drop candidates {len(drop)}, kept {len(keep)}",
              flush=True)
        rows = keep.itertuples()
    for row in rows:
        m = div_ok_mask(axis, dps[dps["cik"] == row.cik],
                        splits.get(_tkey(row.ticker)))
        if all_stocks or bool(m.any()):
            div_ok[row.ticker] = m
    print(f"[info] div_ok tickers {len(div_ok)}", flush=True)
    px = _load_ohlcv(sorted(div_ok))
    keymap: dict[str, str] = {}
    for tk in sorted(px["ticker"].unique()) if not px.empty else []:
        keymap.setdefault(_tkey(tk), tk)
    divmap: dict[str, list[tuple[str, float]]] = {}
    raws = sorted(set(keymap.values()))
    if raws:
        try:
            con = duckdb.connect(str(US_OHLCV_DB), read_only=True)
            try:
                ddf = con.execute(
                    "SELECT ticker,date,amount FROM stock_dividends"
                    f" WHERE ticker IN ({','.join('?' * len(raws))})",
                    raws).fetchdf()
            finally:
                con.close()
        except Exception:
            ddf = None
        if ddf is not None and not ddf.empty:
            ddf["ticker"] = ddf["ticker"].astype(str)
            ddf["date"] = ddf["date"].astype(str)
            ddf["amount"] = pd.to_numeric(ddf["amount"], errors="coerce")
            ddf = ddf.dropna(subset=["amount"])
            for tk, d, v in zip(ddf["ticker"], ddf["date"], ddf["amount"]):
                divmap.setdefault(_tkey(str(tk)), []).append(
                    (str(d), float(v)))
    data: dict[str, dict] = {}
    for ticker, m in div_ok.items():
        raw = keymap.get(_tkey(ticker))
        if raw is None:
            continue
        g = px[px["ticker"] == raw].set_index("date").reindex(axis)
        arr = {c: pd.to_numeric(g[c], errors="coerce").to_numpy(dtype=float)
               for c in ["open", "high", "low", "close", "trading_value"]}
        cp = pd.Series(arr["close"])
        adv = pd.Series(arr["trading_value"]).rolling(20, min_periods=20).mean().shift(1)
        liq = ((cp.shift(1) >= 1)
               & (adv >= 1_000_000)).fillna(False).to_numpy(dtype=bool)
        elig = liq if all_stocks else m & liq
        if not bool(elig.any()):
            continue
        pairs = divmap.get(_tkey(ticker), [])
        data[ticker] = {**arr, "elig": elig,
                        "hi": _rolling_hi(arr["close"], LOOKBACK),
                        "div": div_array(axis, [p[0] for p in pairs],
                                         [p[1] for p in pairs]),
                        "stable": np.asarray(m, dtype=bool)}
    print(f"[info] eligible tickers {len(data)}", flush=True)
    spy = _load_spy()
    return data, spy


def _build_cells(data: dict, axis: list[str], x_list: list[float],
                 hold_list: list[int],
                 tp_list: list[tuple[str, str, float]] = TP_LIST
                 ) -> dict[tuple[float, int, str], list[dict]]:
    """(X, 보유, 목표) 셀별 전 종목 시뮬레이션."""
    cells: dict[tuple[float, int, str], list[dict]] = {}
    for x in x_list:
        for h in hold_list:
            for label, kind, val in tp_list:
                trades: list[dict] = []
                for ticker, a in data.items():
                    for t in simulate(a["open"], a["high"], a["low"], a["close"],
                                      a["elig"], x, kind, val, h, LOOKBACK):
                        if axis[t["sig_i"]] < START:
                            continue
                        trades.append({"ticker": ticker, **t})
                cells[(x, h, label)] = trades
        print(f"[info] X={x} done", flush=True)
    return cells


def _build_pools(cells: dict, data: dict) -> dict[int, list[str]]:
    """진입일별 eligible 티커 풀 (P1용)."""
    need_days = sorted({t["entry_i"] for ts in cells.values() for t in ts
                        if t["reason"] != "unclosed"})
    return {j: [tk for tk, a in data.items() if a["elig"][j]] for j in need_days}


def _header_lines(x_text: str, hold_text: str) -> list[str]:
    """맨 위 제목·생존편향·적용값 표."""
    return ["# REBOUND — 미국 안정배당주 급락 후 반등 단기 매매", "",
            "생존편향: 현재 상장 종목만 — 상한", "배당 미포함(주가만)", "",
            "## 적용값", "",
            "| 항목 | 값 |", "|---|---|",
            "| 진입 대상 | 신호일 기준 배당 안정(최근 3개 FY DPS 매년 직전 해 95% 이상, 최신 FY end ≥ 신호일−550일·연속, filed < 신호일, SIC 67xx·없음 제외, 전일 종가 ≥ $1, 20일 평균 거래대금 ≥ $1M) |",  # noqa: E501
            "| 급락 신호 | 종가 ≤ 252거래일 최고 종가 × (1−X), 전날 종가는 기준 위 |",
            f"| X | {x_text} |",
            "| 재진입 | 보유 중 신호 무시, 청산 후 기준 위로 올라갔다 다시 하락 시 새 신호 |",
            "| 진입 | 신호 다음 거래일 시가 |",
            "| 목표가 | 고정 +15%/+25%/+40%, 되돌림 1/3·1/2 |",
            "| 목표 체결 | 진입 다음 거래일부터, 시가 ≥ 목표 → 시가, 아니면 고가 ≥ 목표 → 목표가 |",
            f"| 최대 보유 | {hold_text} |",
            "| 손절 | 없음 |", "| 배당 | 제외(주가만) |", "| 비용 | 왕복 0.35% |",
            "| 집계 | 진입일 가중(날짜 평균 → 평균) |", "| 기간 | 2016~현재 |", ""]


def _run_default(axis: list[str], data: dict, spy: dict) -> None:
    """기본 모드 → REBOUND.md (25칸·연도별·P1)."""
    cells = _build_cells(data, axis, X_LIST, [MAX_HOLD])
    pools = _build_pools(cells, data)
    hdr = ["X", "목표", "n", "진입일수", "미청산", "도달률", "평균ret",
           "중앙값", "승률", "평균hold", "mae중앙", "mae하위10%", "SPY초과"]
    lines = _header_lines("30%, 40%, 50%, 60%, 70%",
                          "60거래일(진입일 포함 60번째 거래일 종가)")
    tables = [("## 25칸 (전체)", None, None),
              (f"## 25칸 ({COVID_OUT[0]}~{COVID_OUT[1]} 진입 제외)",
               COVID_OUT[0], COVID_OUT[1])]
    for title, lo, hi_d in tables:
        lines += [title, "", "|" + "|".join(hdr) + "|",
                  "|" + "|".join(["---"] * len(hdr)) + "|"]
        for x in X_LIST:
            for label, _, _ in TP_LIST:
                ts = cells[(x, MAX_HOLD, label)]
                df = _cell_df(ts, axis, spy, lo, hi_d)
                nuncl = sum(1 for t in ts if t["reason"] == "unclosed"
                            and (lo is None or not (lo <= axis[t["entry_i"]] <= hi_d)))
                lines.append("|" + "|".join(
                    [f"{x:.0%}", label, *_cell_row(df, nuncl)]) + "|")
        lines.append("")
    lines += ["## X=40% 연도별 (n·평균ret, 하루 가중)", "",
              "|연도|목표|n|평균ret|", "|---|---|---|---|"]
    for label, _, _ in TP_LIST:
        df40 = _cell_df(cells[(0.40, MAX_HOLD, label)], axis, spy)
        if len(df40):
            for y, g in sorted(df40.groupby(df40["date"].str[:4])):
                lines.append(f"|{y}|{label}|{len(g)}|"
                             f"{_pct(day_weighted_mean(g, 'ret'))}|")
    lines += ["", "## P1 무작위 대조 (200 draws)", "", P1_NOTE, "",
              "|X|목표|draws평균|실제백분위|", "|---|---|---|---|"]
    for x in X_LIST:
        for label, kind, val in TP_LIST:
            ts = cells[(x, MAX_HOLD, label)]
            df = _cell_df(ts, axis, spy)
            actual = day_weighted_mean(df, "ret")
            entries = [t["entry_i"] for t in ts if t["reason"] != "unclosed"]
            mean_d, pct = _placebo(entries, actual, pools, data, kind, val, axis)
            lines.append(f"|{x:.0%}|{label}|{_pct(mean_d)}|{_fmt(pct, 1)}|")
        print(f"[info] P1 X={x} done", flush=True)
    lines.append("")
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[info] wrote {RESULTS_PATH}", flush=True)


def _run_hold(axis: list[str], data: dict, spy: dict) -> None:
    """민감도 모드 → REBOUND_HOLD.md (45칸×2·P1 고정·연도별)."""
    cells = _build_cells(data, axis, SENS_X_LIST, SENS_HOLD_LIST)
    pools = _build_pools(cells, data)
    hdr = ["X", "보유", "목표", "n", "진입일수", "미청산", "도달률",
           "평균ret", "중앙값", "승률", "평균hold", "mae중앙",
           "mae하위10%", "SPY초과"]
    lines = _header_lines("60%, 70%, 80%",
                          "20/60/120거래일(진입일 포함 N번째 거래일 종가)")
    tables = [("## 45칸 (전체)", None, None),
              (f"## 45칸 ({COVID_OUT[0]}~{COVID_OUT[1]} 진입 제외)",
               COVID_OUT[0], COVID_OUT[1])]
    for title, lo, hi_d in tables:
        lines += [title, "", "|" + "|".join(hdr) + "|",
                  "|" + "|".join(["---"] * len(hdr)) + "|"]
        for x in SENS_X_LIST:
            for h in SENS_HOLD_LIST:
                for label, _, _ in TP_LIST:
                    ts = cells[(x, h, label)]
                    df = _cell_df(ts, axis, spy, lo, hi_d)
                    nuncl = sum(1 for t in ts if t["reason"] == "unclosed"
                                and (lo is None or not (lo <= axis[t["entry_i"]] <= hi_d)))
                    lines.append("|" + "|".join(
                        [f"{x:.0%}", str(h), label, *_cell_row(df, nuncl)]) + "|")
        lines.append("")
    fixed_only = [t for t in TP_LIST if t[1] == "fixed"]
    lines += ["", "## P1 무작위 대조 (200 draws, 고정 목표만)", "", P1_NOTE, "",
              "|X|보유|목표|draws평균|실제백분위|", "|---|---|---|---|---|"]
    for x in SENS_X_LIST:
        for h in SENS_HOLD_LIST:
            for label, kind, val in fixed_only:
                ts = cells[(x, h, label)]
                df = _cell_df(ts, axis, spy)
                actual = day_weighted_mean(df, "ret")
                entries = [t["entry_i"] for t in ts if t["reason"] != "unclosed"]
                mean_d, pct = _placebo(entries, actual, pools, data, kind,
                                       val, axis, max_hold=h)
                lines.append(f"|{x:.0%}|{h}|{label}|{_pct(mean_d)}|{_fmt(pct, 1)}|")
        print(f"[info] P1 X={x} done", flush=True)
    lines += ["", "## X=70% +25% 연도별 (n·평균ret, 하루 가중)", "",
              "|연도|보유|n|평균ret|", "|---|---|---|---|"]
    for h in SENS_HOLD_LIST:
        dfh = _cell_df(cells[(0.70, h, "+25%")], axis, spy)
        if len(dfh):
            for y, g in sorted(dfh.groupby(dfh["date"].str[:4])):
                lines.append(f"|{y}|{h}|{len(g)}|"
                             f"{_pct(day_weighted_mean(g, 'ret'))}|")
    lines.append("")
    HOLD_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOLD_RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[info] wrote {HOLD_RESULTS_PATH}", flush=True)


def _run_focus(axis: list[str], data: dict, spy: dict) -> None:
    """focus 모드 → REBOUND_FOCUS70.md (X=70%, 코로나 제외, 18칸×3·연도별·거래목록)."""
    lo, hi_d = FOCUS_COVID_OUT
    cells = _build_cells(data, axis, [FOCUS_X], FOCUS_HOLD_LIST, FOCUS_TP_LIST)
    cells = {k: drop_entry_window(ts, axis, lo, hi_d) for k, ts in cells.items()}
    pools = _build_pools(cells, data)
    hdr = ["보유", "목표", "n", "진입일수", "미청산", "도달률", "평균ret",
           "중앙값", "승률", "평균hold", "mae중앙", "mae하위10%", "SPY초과"]
    lines = _header_lines("70%",
                          "20/60/120거래일(진입일 포함 N번째 거래일 종가)")
    lines = ["| 목표가 | 만료만(목표 없음, 기간 만료 종가 청산), 고정 +15%/+25%/+40%/+60%/+100% |"  # noqa: E501
             if l.startswith("| 목표가 |") else l for l in lines]
    ix = next(i for i, l in enumerate(lines) if l.startswith("| X |"))
    lines[ix:ix + 1] = [lines[ix], "| 코로나 제외 | 2020-02-01~2020-06-30 진입 제외 |"]
    lines += [f"## 18칸 (보유×목표, {lo}~{hi_d} 진입 제외)", "",
              "|" + "|".join(hdr) + "|",
              "|" + "|".join(["---"] * len(hdr)) + "|"]
    for h in FOCUS_HOLD_LIST:
        for label, _, _ in FOCUS_TP_LIST:
            ts = cells[(FOCUS_X, h, label)]
            df = _cell_df(ts, axis, spy)
            nuncl = sum(1 for t in ts if t["reason"] == "unclosed")
            lines.append("|" + "|".join(
                [str(h), label, *_cell_row(df, nuncl)]) + "|")
    lines += ["", f"## 18칸 (종목 첫 진입만, {lo}~{hi_d} 진입 제외)", "",
              "|" + "|".join([*hdr, "고유 종목 수"]) + "|",
              "|" + "|".join(["---"] * (len(hdr) + 1)) + "|"]
    for h in FOCUS_HOLD_LIST:
        for label, _, _ in FOCUS_TP_LIST:
            ts = first_entry_per_ticker(cells[(FOCUS_X, h, label)])
            df = _cell_df(ts, axis, spy)
            nuncl = sum(1 for t in ts if t["reason"] == "unclosed")
            uniq = len({t["ticker"] for t in ts if t["reason"] != "unclosed"})
            lines.append("|" + "|".join(
                [str(h), label, *_cell_row(df, nuncl), str(uniq)]) + "|")
    lines += ["", "## P1 무작위 대조 (200 draws, 만료만 포함)", "",
              "만료만 포함 — 무작위 종목도 같은 기간 보유라 공정", "",
              "|보유|목표|draws평균|실제백분위|", "|---|---|---|---|"]
    for h in FOCUS_HOLD_LIST:
        for label, kind, val in FOCUS_TP_LIST:
            ts = cells[(FOCUS_X, h, label)]
            df = _cell_df(ts, axis, spy)
            actual = day_weighted_mean(df, "ret")
            entries = [t["entry_i"] for t in ts if t["reason"] != "unclosed"]
            mean_d, pct = _placebo(entries, actual, pools, data, kind,
                                   val, axis, max_hold=h)
            lines.append(f"|{h}|{label}|{_pct(mean_d)}|{_fmt(pct, 1)}|")
        print(f"[info] P1 보유={h} done", flush=True)
    lines += ["", "## 연도별 (보유 60·120, 만료만·+25%·+40%)", "",
              "|연도|보유|목표|n|고유 종목 수|평균ret|승률|",
              "|---|---|---|---|---|---|---|"]
    for h in (60, 120):
        for label in ("만료만", "+25%", "+40%"):
            ts = [t for t in cells[(FOCUS_X, h, label)]
                  if t["reason"] != "unclosed"]
            years: dict[str, list[dict]] = {}
            for t in ts:
                years.setdefault(axis[t["entry_i"]][:4], []).append(t)
            for y in sorted(years):
                g = _cell_df(years[y], axis, spy)
                uniq = len({t["ticker"] for t in years[y]})
                lines.append(f"|{y}|{h}|{label}|{len(g)}|{uniq}|"
                             f"{_pct(day_weighted_mean(g, 'ret'))}|"
                             f"{_pct(day_win_rate(g, 'ret'))}|")
    lines += ["", "## 거래 목록 (보유 120, +25%)", "",
              "|ticker|신호일|진입일|진입가|청산일|청산가|reason|ret|mae|",
              "|---|---|---|---|---|---|---|---|---|"]
    tl = sorted((t for t in cells[(FOCUS_X, 120, "+25%")]
                 if t["reason"] != "unclosed"),
                key=lambda t: t["sig_i"])
    for t in tl:
        lines.append("|" + "|".join(
            [t["ticker"], axis[t["sig_i"]], axis[t["entry_i"]],
             _fmt(t["entry"]), axis[t["exit_i"]], _fmt(t["exit_px"]),
             t["reason"], _pct(t["ret"]), _pct(t["mae"])]) + "|")
    lines.append("")
    FOCUS_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FOCUS_RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[info] wrote {FOCUS_RESULTS_PATH}", flush=True)


def _run_regime(axis: list[str], data: dict, spy: dict,
               out_path: Path = REGIME_RESULTS_PATH,
               title: str = "# REBOUND_REGIME — 시장 상태별 X + 10칸 계좌",
               universe_note: str | None = None) -> None:
    """regime 모드 → REBOUND_REGIME.md (시장 상태별 X + 10칸 계좌)."""
    n_days = len(axis)
    s = next(i for i, d in enumerate(axis) if d >= START)
    spy_arr = pd.Series([spy.get(d, np.nan) for d in axis],
                        dtype=float).ffill().to_numpy()
    print(f"[info] regime SPY {axis[0]}~{axis[-1]}", flush=True)
    reg = {"hi252": market_regime(spy_arr, "hi252"),
           "fast20": market_regime(spy_arr, "fast20")}
    print(f"[info] regime days hi252={int(reg['hi252'][s:].sum())} "
          f"fast20={int(reg['fast20'][s:].sum())}", flush=True)
    strats: dict[str, tuple] = {
        "①1년고점": (np.where(reg["hi252"], 0.70, 0.50), "hi252"),
        "②속도20일": (np.where(reg["fast20"], 0.70, 0.50), "fast20"),
        "고정50": (0.50, None), "고정70": (0.70, None)}
    all_trades: dict[str, list[dict]] = {}
    for name, (xx, rk) in strats.items():
        trades: list[dict] = []
        for ticker, a in data.items():
            for t in simulate(a["open"], a["high"], a["low"], a["close"],
                              a["elig"], xx, "fixed", 0.25, 120, LOOKBACK):
                si = t["sig_i"]
                if axis[si] < START:
                    continue
                trades.append({"ticker": ticker,
                               "depth": float(a["close"][si] / a["hi"][si] - 1),
                               "regime_flag": bool(reg[rk][si]) if rk else False,
                               **t})
        all_trades[name] = trades
        print(f"[info] {name} signals {len(trades)}", flush=True)
    close_by = {tk: a["close"] for tk, a in data.items()}
    div_by_ticker = {tk: a["div"] for tk, a in data.items()}
    _spy_div_map = _load_spy_div()
    spy_div = div_array(axis, list(_spy_div_map),
                        [_spy_div_map[d] for d in _spy_div_map])
    sp_ret = pd.Series(spy_arr[s:] / spy_arr[s - 1:-1] - 1, index=axis[s:])
    sp70_ret = sp_ret * 0.7
    sp_ret_div = pd.Series((spy_arr[s:] + spy_div[s:] * (1 - DIV_TAX))
                           / spy_arr[s - 1:-1] - 1, index=axis[s:])
    sp70_ret_div = sp_ret_div * 0.7
    sp_summ = {("SPY", "주가만"): summary(sp_ret),
               ("SPY70%", "주가만"): summary(sp70_ret),
               ("SPY", "배당 포함"): summary(sp_ret_div),
               ("SPY70%", "배당 포함"): summary(sp70_ret_div)}
    acct: dict[tuple[str, str, str], dict] = {}
    for ver, win in (("포함", None), ("제외", FOCUS_COVID_OUT)):
        for name in strats:
            ts = all_trades[name]
            if win is not None:
                ts = drop_entry_window(ts, axis, win[0], win[1])
            closed = [t for t in ts if t["reason"] != "unclosed"]
            for dvv in ("주가만", "배당 포함"):
                dd = None if dvv == "주가만" else div_by_ticker
                r = run_slots(closed, close_by, n_days, div_by_ticker=dd)
                ret = pd.Series(r["equity"][s:] / r["equity"][s - 1:-1] - 1,
                                index=axis[s:])
                sm = summary(ret)
                pools = _build_pools({"t": r["taken_trades"]}, data)
                draws = _placebo_slots(r["taken_trades"], pools, data, axis,
                                       n_days, s, div_by_ticker=dd)
                _, pct = placebo_percentile(sm["cagr"], draws)
                acct[(name, ver, dvv)] = {"run": r, "ret": ret, "summ": sm,
                                          "pct": pct, "closed": closed}
                print(f"[info] {name}/{ver}/{dvv} taken={r['taken']} "
                      f"skipped={r['skipped']} pct={_fmt(pct, 1)}", flush=True)
    print("[info] writing tables", flush=True)
    univ = ("신호일 기준 배당 안정(최근 3개 FY DPS 매년 직전 해 95% 이상,"
            " 최신 FY end ≥ 신호일−550일·연속, filed < 신호일,"
            " SIC 67xx·없음 제외, 전일 종가 ≥ $1, 20일 평균 거래대금 ≥ $1M)"
            if universe_note is None else universe_note)
    lines = [title, "",
             "생존편향: 현재 상장 종목만 — 상한",
             "배당: 주가만 / 배당 포함(배당락일 현금 수령, 원천징수 15%) 두 버전. 양도세 미포함", "",
             "## 적용값", "",
             "| 항목 | 값 |", "|---|---|",
             f"| 진입 대상 | {univ} |",
             "| 급락기 ① 1년 고점 | 신호일 SPY 종가 ≤ 최근 252거래일 최고 종가 × 0.9 |",
             "| 급락기 ② 속도 | SPY 종가 ≤ 최근 20거래일 최고 종가 × 0.9 인 날부터 60거래일 |",
             "| X | 평소 50%, 급락기 70% |",
             "| 급락 신호 | 종가 ≤ 1년 고점 × (1−X당일), 전날 종가 > 전날 1년 고점 × (1−X당일) |",
             "| 진입 | 신호 다음 거래일 시가 |",
             "| 청산 | +25% 또는 120거래일째 종가, 손절 없음 |",
             "| 계좌 | 10칸, 칸당 전일 평가액 10%, 남는 돈 현금(수익 0) |",
             "| 비용 | 진입·청산 각 0.175% |",
             "| 배당 | 배당락일 기준 보유분, 원천징수 15% 후 현금, 재투자 없음(다음 진입 자금으로만) |",  # noqa: E501
             "| 신호 몰림 | 같은 진입일이면 신호일 낙폭(종가/1년 고점) 큰 순 |",
             "| 지표 | 계좌 연 복리·일별 MDD·샤프, 평균 투자 비중, 체결·건너뜀, 연도별 |",
             "| 기간 | 2016~현재 |",
             "| 코로나 제외 | 2020-02-01~2020-06-30 진입 제외 버전 병기 |", "",
             "## A 급락기 날짜 (START 이후)", "",
             "| 상태 | 급락기 일수 | 구간 |", "|---|---|---|"]
    for key, label in (("hi252", "①1년고점"), ("fast20", "②속도20일")):
        flag = reg[key][s:]
        spans: list[str] = []
        idx = np.flatnonzero(flag)
        if idx.size:
            st = pv = int(idx[0])
            for j in (int(v) for v in idx[1:]):
                if j == pv + 1:
                    pv = j
                else:
                    spans.append(f"{axis[s + st]}~{axis[s + pv]}")
                    st = pv = j
            spans.append(f"{axis[s + st]}~{axis[s + pv]}")
        lines.append(f"|{label}|{int(flag.sum())}일|"
                     f"{', '.join(spans) if spans else '-'}|")
    lines.append("")
    for ver in ("포함", "제외"):
        for dvv in ("주가만", "배당 포함"):
            lines += [f"## B 계좌 성과 (코로나 {ver}, {dvv})", "",
                      "|전략|연복리|일별MDD|샤프|평균 투자비중|체결|건너뜀|무작위 대조 백분위|",
                      "|---|---|---|---|---|---|---|---|"]
            for name in strats:
                a = acct[(name, ver, dvv)]
                sm = a["summ"]
                lines.append("|" + "|".join(
                    [name, _pct(sm["cagr"]), _pct(sm["mdd"]),
                     _fmt(sm["sharpe"]),
                     _pct(float(np.mean(a["run"]["invested"][s:]))),
                     str(a["run"]["taken"]), str(a["run"]["skipped"]),
                     _fmt(a["pct"], 1)]) + "|")
            for bn in ("SPY", "SPY70%"):
                sm = sp_summ[(bn, dvv)]
                lines.append("|" + "|".join(
                    [bn, _pct(sm["cagr"]), _pct(sm["mdd"]),
                     _fmt(sm["sharpe"]),
                     "-", "-", "-", "-"]) + "|")
            lines.append("")
    lines += ["## C 연도별 수익 (코로나 포함, 연 복리)", "",
              "|연도|①1년고점|②속도20일|고정50|고정70|SPY|SPY70%|",
              "|---|---|---|---|---|---|---|"]
    cols = ["①1년고점", "②속도20일", "고정50", "고정70", "SPY", "SPY70%"]
    rets = {name: acct[(name, "포함", "주가만")]["ret"] for name in strats}
    rets["SPY"], rets["SPY70%"] = sp_ret, sp70_ret
    ytab = {k: yearly(v) for k, v in rets.items()}
    for y in sorted(ytab["SPY"].index):
        lines.append("|" + "|".join(
            [y, *(_pct(ytab[k].get(y, np.nan)) for k in cols)]) + "|")
    lines += ["", "## C 연도별 수익 (코로나 포함·배당 포함, 연 복리)", "",
              "|연도|①1년고점|②속도20일|고정50|고정70|SPY|SPY70%|",
              "|---|---|---|---|---|---|---|"]
    rets_d = {name: acct[(name, "포함", "배당 포함")]["ret"] for name in strats}
    rets_d["SPY"], rets_d["SPY70%"] = sp_ret_div, sp70_ret_div
    ytab_d = {k: yearly(v) for k, v in rets_d.items()}
    for y in sorted(ytab_d["SPY"].index):
        lines.append("|" + "|".join(
            [y, *(_pct(ytab_d[k].get(y, np.nan)) for k in cols)]) + "|")
    lines += ["", "## D 신호 구간 분리 (평소/급락기, n=청산분)", "",
              "|전략|코로나|구간|전체 n|전체 평균ret|전체 승률|체결 n|체결 평균ret|"
              "체결 평균ret(배당 포함)|",
              "|---|---|---|---|---|---|---|---|---|"]
    for name in ("①1년고점", "②속도20일"):
        for ver in ("포함", "제외"):
            a = acct[(name, ver, "주가만")]
            for fl, glabel in ((False, "평소"), (True, "급락기")):
                g = [t for t in a["closed"] if t["regime_flag"] == fl]
                df = pd.DataFrame([{"date": axis[t["entry_i"]], "ret": t["ret"]}
                                   for t in g], columns=["date", "ret"])
                tk = [t for t in a["run"]["taken_trades"]
                      if t["regime_flag"] == fl]
                dft = pd.DataFrame([{"date": axis[t["entry_i"]], "ret": t["ret"]}
                                    for t in tk], columns=["date", "ret"])
                rows_rd = []
                for t in tk:
                    arr = div_by_ticker.get(t["ticker"])
                    dv = trade_div(arr, t["entry_i"], t["exit_i"]) \
                        if arr is not None else 0.0
                    rd = ((float(t["exit_px"]) + dv * (1 - DIV_TAX))
                          / float(t["entry"]) - 1 - COST)
                    rows_rd.append({"date": axis[t["entry_i"]],
                                    "retdiv": rd})
                dfrd = pd.DataFrame(rows_rd, columns=["date", "retdiv"])
                lines.append("|" + "|".join(
                    [name, ver, glabel, str(len(df)),
                     _pct(day_weighted_mean(df, "ret")),
                     _pct(day_win_rate(df, "ret")),
                     str(len(dft)),
                     _pct(day_weighted_mean(dft, "ret")),
                     _pct(day_weighted_mean(dfrd, "retdiv"))]) + "|")
    lines += ["", "## E 체결 건 배당 기여 (코로나 포함)", "",
              "|전략|체결 n|배당 받은 건 비율|건당 평균 배당(세후, 진입가 대비)|",
              "|---|---|---|---|"]
    for name in strats:
        tke = acct[(name, "포함", "주가만")]["run"]["taken_trades"]
        per = []
        got = 0
        for t in tke:
            arr = div_by_ticker.get(t["ticker"])
            dv = trade_div(arr, t["entry_i"], t["exit_i"]) \
                if arr is not None else 0.0
            if dv > 0:
                got += 1
            per.append(dv * (1 - DIV_TAX) / float(t["entry"]))
        lines.append("|" + "|".join(
            [name, str(len(tke)),
             _pct(got / len(tke)) if tke else "-",
             _pct(float(np.mean(per))) if per else "-"]) + "|")
    lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[info] wrote {out_path}", flush=True)


def _run_cut(axis: list[str], data: dict, spy: dict) -> None:
    """cut 모드 → DIV_CUT.md (H2: 급락 뒤 배당 삭감 경향)."""
    divs = {tk: data[tk]["div"] for tk in sorted(data)}
    spy_arr = pd.Series([spy.get(d, np.nan) for d in axis],
                        dtype=float).ffill().to_numpy()
    spy_hi = _rolling_hi(spy_arr, LOOKBACK)
    rows: list[dict] = []
    months = month_end_idx(axis, CUT_LO, CUT_HI)
    for num, i in enumerate(months):
        for ticker in sorted(data):
            a = data[ticker]
            if not bool(a["elig"][i]):
                continue
            px = float(a["close"][i])
            if not np.isfinite(px):
                continue
            prev, nxt = window_sum(divs[ticker], i)
            if not (prev > 0) or not np.isfinite(nxt):
                continue
            dd = px / float(a["hi"][i]) - 1
            bucket = dd_bucket4(dd)
            if bucket is None:
                continue
            ratio = nxt / prev
            j = i + LOOKBACK
            if j < len(a["close"]) and np.isfinite(a["close"][j]):
                ret1y = float(a["close"][j]) / px - 1
                tot1y = ((float(a["close"][j]) + nxt * (1 - DIV_TAX)) / px - 1)
            else:
                ret1y = float("nan")
                tot1y = float("nan")
            rows.append({"date": axis[i], "ticker": ticker,
                         "dd": dd, "bucket": bucket,
                         "yld": prev / px, "ratio": ratio,
                         "cut": 1.0 if ratio < 0.9 else 0.0,
                         "susp": 1.0 if nxt == 0 else 0.0,
                         "special": special_suspect(divs[ticker], i),
                         "mkt": bool(spy_arr[i] <= spy_hi[i] * 0.9),
                         "ret1y": ret1y, "tot1y": tot1y})
        print(f"[info] cut {axis[i]} ({num + 1}/{len(months)})", flush=True)
    cols = ["date", "ticker", "dd", "bucket", "yld", "ratio", "cut", "susp",
            "special", "ret1y", "tot1y", "mkt"]
    df = pd.DataFrame(rows, columns=cols)
    print(f"[info] cut obs {len(df)}", flush=True)
    lines = ["# DIV_CUT — 주가 급락 뒤 배당 삭감 경향 (H2)", "",
             "생존편향: 현재 상장만 — 삭감률 하한",
             "집계: 같은 월 평균 → 월 평균", "",
             "## 적용값", "",
             "| 항목 | 값 |", "|---|---|",
             "| 시장 | 미국 (`stock_dividends` 배당락일 이력 2015~) |",
             "| 대상 | 관측월 말 기준 1절 배당 안정 조건 + 유동성 (rebound `_load_data` eligible 그대로) |",  # noqa: E501
             "| 관측 | 2016-01 ~ 2025-10 매월 말(그달 마지막 거래일) 종목 1건, 직전 12개월(252거래일) 배당 합 > 0 |",  # noqa: E501
             "| 낙폭 구간 | 월말 종가 / 252일 최고 종가 − 1: 0~−20 / −20~−40 / −40~−60 / −60% 초과 |",  # noqa: E501
             "| 삭감 | 다음 252거래일 배당 합 ÷ 직전 252거래일 배당 합 < 0.9, 중단 = 0 |",
             "| 특별배당 의심 | 직전 창 배당 3회 이상이고 최대 1회 > 중앙값 × 2 → 표시·제외 버전 |",  # noqa: E501
             "| 시장 상태 | 관측일 SPY 종가 ≤ 252일 고점 × 0.9 → 시장 동반 하락, 아니면 개별 하락 |",  # noqa: E501
             "| 이후 수익 | 다음 252거래일 주가 수익, 배당 포함(세후 ×0.85) 수익 — 삭감/유지별 |",
             "| 집계 | 같은 월 먼저 평균 → 월 평균 (`stats.day_weighted_mean`) |", ""]
    hdr_a = ["구간", "관측 n", "월 수", "고유 종목", "시가배당률 중앙값",
             "삭감률", "중단률", "ratio 중앙값"]
    for title, base in (("## A 구간별 (전체)", df),
                        ("## A 구간별 (특별배당 의심 제외)",
                         df[~df["special"]] if len(df) else df)):
        lines += [title, "", "|" + "|".join(hdr_a) + "|",
                  "|" + "|".join(["---"] * len(hdr_a)) + "|"]
        for b in CUT_BUCKETS:
            g = base[base["bucket"] == b] if len(base) else base
            ym = g["date"].str[:6].nunique() if len(g) else 0
            uq = g["ticker"].nunique() if len(g) else 0
            lines.append("|" + "|".join(
                [b, str(len(g)), str(ym), str(uq),
                 _pct(_med(g["yld"])), _pct(day_weighted_mean(g, "cut")),
                 _pct(day_weighted_mean(g, "susp")),
                 _fmt(_med(g["ratio"]))]) + "|")
        lines.append("")
    lines += ["## B 구간 × 시장 상태", "",
              "|구간|시장 상태|n|고유 종목|삭감률|중단률|",
              "|---|---|---|---|---|---|"]
    for b in CUT_BUCKETS:
        for flag, label in ((True, "동반"), (False, "개별")):
            g = df[(df["bucket"] == b) & (df["mkt"] == flag)] if len(df) \
                else df
            uq = g["ticker"].nunique() if len(g) else 0
            lines.append("|" + "|".join(
                [b, label, str(len(g)), str(uq),
                 _pct(day_weighted_mean(g, "cut")),
                 _pct(day_weighted_mean(g, "susp"))]) + "|")
    lines += ["", "## C 구간 × 삭감 여부", "",
              "|구간|삭감 여부|n|ret1y 평균|ret1y 중앙값|tot1y 평균|",
              "|---|---|---|---|---|---|"]
    for b in CUT_BUCKETS:
        for flag, label in ((1.0, "삭감"), (0.0, "유지")):
            g = df[(df["bucket"] == b) & (df["cut"] == flag)] if len(df) \
                else df
            lines.append("|" + "|".join(
                [b, label, str(len(g)),
                 _pct(day_weighted_mean(g, "ret1y")),
                 _pct(_med(g["ret1y"])),
                 _pct(day_weighted_mean(g, "tot1y"))]) + "|")
    lines += ["", "## D 연도별", "",
              "|연도|구간|n|삭감률|", "|---|---|---|---|"]
    if len(df):
        df = df.copy()
        df["yy"] = df["date"].str[:4]
        for y in sorted(df["yy"].unique()):
            for msk, label in ((df["bucket"].isin(["-40~-60", "-60+"]),
                                "-40~-60·-60+ 합침"),
                               (df["bucket"] == "0~-20", "0~-20")):
                g = df[(df["yy"] == y) & msk]
                lines.append("|" + "|".join(
                    [y, label, str(len(g)),
                     _pct(day_weighted_mean(g, "cut"))]) + "|")
    lines += ["", "## E -60+ 삭감 건 (종목당 첫 관측 1건)", "",
              "|ticker|date|dd|yld|ratio|mkt|ret1y|",
              "|---|---|---|---|---|---|---|"]
    if len(df):
        e = df[(df["bucket"] == "-60+") & (df["cut"] == 1.0)]
        e = e.sort_values("date").drop_duplicates("ticker", keep="first")
        for r in e.sort_values("date").itertuples():
            lines.append("|" + "|".join(
                [r.ticker, r.date, _pct(r.dd), _pct(r.yld), _fmt(r.ratio),
                 "동반" if r.mkt else "개별", _pct(r.ret1y)]) + "|")
    lines.append("")
    CUT_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUT_RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[info] wrote {CUT_RESULTS_PATH}", flush=True)


def _run_cutreb(axis: list[str], data: dict, spy: dict) -> None:
    """cutreb 모드 → REBOUND_CUT.md (−70% 반등 × 배당 삭감)."""
    tp_list = [("만료만", "none", 0), ("+25%", "fixed", 0.25)]
    hold_list = [60, 120]
    cells = _build_cells(data, axis, [0.70], hold_list, tp_list)
    cells = {k: [t for t in ts if t["reason"] != "unclosed"]
             for k, ts in cells.items()}
    spy_arr = pd.Series([spy.get(d, np.nan) for d in axis],
                        dtype=float).ffill().to_numpy()
    spy_hi = _rolling_hi(spy_arr, LOOKBACK)
    enriched: dict[tuple, list[dict]] = {}
    for key, ts in cells.items():
        out: list[dict] = []
        for t in ts:
            a = data[t["ticker"]]
            div = a["div"]
            sig_i, e, k = t["sig_i"], t["entry_i"], t["exit_i"]
            prev, nxt = window_sum(div, sig_i)
            if not np.isfinite(prev) or prev == 0 or not np.isfinite(nxt):
                cls = "판정불가"
            elif nxt / prev < 0.9:
                cls = "삭감"
            else:
                cls = "유지"
            det = detect_cut(div, sig_i, e, k)
            entry = float(t["entry"])
            dv = trade_div(div, e, k)
            ret_div = float(t["ret"]) + dv * (1 - DIV_TAX) / entry
            if det is not None:
                sold = sell_on_detect(t, a["open"], det[0])
            else:
                sold = dict(t)
            dv_s = trade_div(div, e, sold["exit_i"])
            ret_div_s = float(sold["ret"]) + dv_s * (1 - DIV_TAX) / entry
            mkt = bool(spy_arr[sig_i] <= spy_hi[sig_i] * 0.9)
            et = dict(t)
            et["cls"] = cls
            et["det"] = det
            et["det_kind"] = det[1] if det else None
            et["ret_div"] = ret_div
            et["sold"] = sold
            et["ret_div_sold"] = ret_div_s
            et["mkt"] = mkt
            out.append(et)
        enriched[key] = out
    vers = [("포함", None), ("제외", FOCUS_COVID_OUT)]
    vtrades: dict[tuple, list[dict]] = {}
    for ver, win in vers:
        for h in hold_list:
            for label, _, _ in tp_list:
                ts = enriched[(0.70, h, label)]
                if win is not None:
                    ts = drop_entry_window(ts, axis, win[0], win[1])
                vtrades[(ver, h, label)] = ts
    lines = ["# REBOUND_CUT — −70% 반등 × 배당 삭감", "",
             "생존편향: 현재 상장만 — 상한",
             "사후 분류는 미래 정보(실매매 불가)", "",
             "## 적용값", "",
             "| 항목 | 값 |", "|---|---|",
             "| 거래 | X 70%, 보유 60·120거래일, 목표 만료만·+25%, 코로나(2020-02-01~2020-06-30 진입) 포함·제외 |",  # noqa: E501
             "| 사후 분류 | 신호일 기준 다음 252일 배당 합 ÷ 직전 252일 합 < 0.9 → 삭감, 직전 합 0·비유한 또는 다음 합 비유한 → 판정불가 |",  # noqa: E501
             "| 보유 중 감지 | 기준 = 신호일 이전 마지막 배당 금액·최근 최대 4회 간격 중앙값. 보유 중 배당 < 기준×0.9 → 삭감 감지(당일, 이후 기준 갱신). 마지막 배당 뒤 간격×1.5 경과 → 중단 감지. 이력 2회 미만이면 감지 안 함 |",  # noqa: E501
             "| 감지 매도 | 감지일 다음 거래일 시가 청산 (감지일+1 ≤ 원래 청산일, 시가 유한·>0). 실제 삭감 공시는 배당락 전이라 보수적 |",  # noqa: E501
             "| 배당 | 주가만 / 보유 중 배당 세후 ×0.85 포함 두 열 |",
             "| 시장 상태 | 신호일 SPY 종가 ≤ 252일 고점 × 0.9 → 동반 |", ""]
    lines += ["## A 사후 분류별 (하루 가중)", "",
              "|코로나|보유|목표|분류|n|고유 종목|평균ret|중앙값|승률|SPY초과|평균ret(배당 포함)|",  # noqa: E501
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for ver, _ in vers:
        for h in hold_list:
            for label, _, _ in tp_list:
                g_all = vtrades[(ver, h, label)]
                for cls in ("삭감", "유지", "판정불가"):
                    g = [t for t in g_all if t["cls"] == cls]
                    df = _cell_df(g, axis, spy)
                    uq = len({t["ticker"] for t in g})
                    dfd = pd.DataFrame(
                        [{"date": axis[t["entry_i"]], "retdiv": t["ret_div"]}
                         for t in g], columns=["date", "retdiv"])
                    lines.append("|" + "|".join(
                        [ver, str(h), label, cls, str(len(g)), str(uq),
                         _pct(day_weighted_mean(df, "ret")),
                         _pct(day_median(df, "ret")),
                         _pct(day_win_rate(df, "ret")),
                         _pct(day_weighted_mean(df, "ex")),
                         _pct(day_weighted_mean(dfd, "retdiv"))]) + "|")
    lines += ["", "## B 보유 중 감지", "",
              "|코로나|보유|목표|n|감지율|cut율|omit율|감지까지 평균 거래일|삭감 중 감지율|유지 중 감지율(오감지)|",  # noqa: E501
              "|---|---|---|---|---|---|---|---|---|---|"]
    for ver, _ in vers:
        for h in hold_list:
            for label, _, _ in tp_list:
                g = vtrades[(ver, h, label)]
                n = len(g)
                det = [t for t in g if t["det"] is not None]
                nc = sum(1 for t in g if t["det_kind"] == "cut")
                no = sum(1 for t in g if t["det_kind"] == "omit")
                days = [t["det"][0] - t["entry_i"] for t in det]
                avg_d = float(np.mean(days)) if days else float("nan")
                g_cut = [t for t in g if t["cls"] == "삭감"]
                g_kep = [t for t in g if t["cls"] == "유지"]
                r_cut = (sum(1 for t in g_cut if t["det"] is not None)
                         / len(g_cut)) if g_cut else float("nan")
                r_kep = (sum(1 for t in g_kep if t["det"] is not None)
                         / len(g_kep)) if g_kep else float("nan")
                lines.append("|" + "|".join(
                    [ver, str(h), label, str(n),
                     _pct(len(det) / n) if n else "-",
                     _pct(nc / n) if n else "-",
                     _pct(no / n) if n else "-",
                     _fmt(avg_d, 1), _pct(r_cut), _pct(r_kep)]) + "|")
    lines += ["", "## C 감지 매도 비교 (하루 가중)", "",
              "|코로나|보유|목표|범위|n|원래 평균ret|매도 평균ret|원래 중앙값|매도 중앙값|원래 승률|매도 승률|원래 SPY초과|매도 SPY초과|원래 배당포함|매도 배당포함|",  # noqa: E501
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for ver, _ in vers:
        for h in hold_list:
            for label, _, _ in tp_list:
                g_all = vtrades[(ver, h, label)]
                for scope, g in (("전체", g_all),
                                 ("감지분", [t for t in g_all
                                             if t["det"] is not None])):
                    sold_list = [t["sold"] for t in g]
                    df_o = _cell_df(g, axis, spy)
                    df_s = _cell_df(sold_list, axis, spy)
                    dfd_o = pd.DataFrame(
                        [{"date": axis[t["entry_i"]], "retdiv": t["ret_div"]}
                         for t in g], columns=["date", "retdiv"])
                    dfd_s = pd.DataFrame(
                        [{"date": axis[t["entry_i"]],
                          "retdiv": t["ret_div_sold"]} for t in g],
                        columns=["date", "retdiv"])
                    lines.append("|" + "|".join(
                        [ver, str(h), label, scope, str(len(g)),
                         _pct(day_weighted_mean(df_o, "ret")),
                         _pct(day_weighted_mean(df_s, "ret")),
                         _pct(day_median(df_o, "ret")),
                         _pct(day_median(df_s, "ret")),
                         _pct(day_win_rate(df_o, "ret")),
                         _pct(day_win_rate(df_s, "ret")),
                         _pct(day_weighted_mean(df_o, "ex")),
                         _pct(day_weighted_mean(df_s, "ex")),
                         _pct(day_weighted_mean(dfd_o, "retdiv")),
                         _pct(day_weighted_mean(dfd_s, "retdiv"))]) + "|")
    lines += ["", "## D 시장 상태 × 사후 분류 (코로나 포함, 120일·+25%)", "",
              "|시장 상태|분류|n|평균ret|승률|",
              "|---|---|---|---|---|"]
    g_d = enriched[(0.70, 120, "+25%")]
    for mkt, mlabel in ((True, "동반"), (False, "개별")):
        for cls in ("삭감", "유지", "판정불가"):
            g = [t for t in g_d if t["mkt"] == mkt and t["cls"] == cls]
            df = _cell_df(g, axis, spy)
            lines.append("|" + "|".join(
                [mlabel, cls, str(len(g)),
                 _pct(day_weighted_mean(df, "ret")),
                 _pct(day_win_rate(df, "ret"))]) + "|")
    lines.append("")
    CUTREB_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUTREB_RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[info] wrote {CUTREB_RESULTS_PATH}", flush=True)


def _run_univ(axis: list[str], data: dict, spy: dict) -> None:
    """univ 모드 → REBOUND_UNIV.md (전 종목 −70%, 신호일 배당 그룹 비교)."""
    tp_list = [("만료만", "none", 0), ("+25%", "fixed", 0.25)]
    hold_list = [60, 120]
    groups = ("안정배당", "불안정배당", "무배당")
    cells = _build_cells(data, axis, [0.70], hold_list, tp_list)
    cells = {k: [t for t in ts if t["reason"] != "unclosed"]
             for k, ts in cells.items()}
    spy_arr = pd.Series([spy.get(d, np.nan) for d in axis],
                        dtype=float).ffill().to_numpy()
    spy_hi = _rolling_hi(spy_arr, LOOKBACK)
    for key, ts in cells.items():
        for t in ts:
            a = data[t["ticker"]]
            prev, _ = window_sum(a["div"], t["sig_i"])
            t["grp"] = div_group(bool(a["stable"][t["sig_i"]]), prev)
            t["mkt"] = bool(spy_arr[t["sig_i"]] <= spy_hi[t["sig_i"]] * 0.9)
        print(f"[info] univ {key} n={len(ts)}", flush=True)
    vers = [("포함", None), ("제외", FOCUS_COVID_OUT)]
    vtrades: dict[tuple, list[dict]] = {}
    for ver, win in vers:
        for h in hold_list:
            for label, _, _ in tp_list:
                ts = cells[(0.70, h, label)]
                if win is not None:
                    ts = drop_entry_window(ts, axis, win[0], win[1])
                vtrades[(ver, h, label)] = ts
    lines = ["# REBOUND_UNIV — 전 종목 −70% 반등 × 신호일 배당 그룹", "",
             "생존편향: 현재 상장 종목만 — 상폐가 많을 무배당 그룹이 가장 과대평가",
             "배당 미포함(주가만)", "",
             "## 적용값", "",
             "| 항목 | 값 |", "|---|---|",
             "| 유니버스 | `_load_companies` 에서 SIC 67xx·없음 제외, 유동성(전일 종가 ≥ $1, 20일 평균 거래대금 ≥ $1M)만 |",  # noqa: E501
             "| 후보 사전 필터 | 2015 이후 종가 ≤ 252일 최고 종가 × 0.3인 날이 한 번이라도 있는 티커만 로드 (메모리, 결과 불변) |",  # noqa: E501
             "| 그룹(신호일) | 안정배당 = 기존 배당 안정 마스크, 불안정배당 = 아니고 직전 252일 배당 합 > 0, 무배당 = 합 0 |",  # noqa: E501
             "| 거래 | X 70%, 보유 60·120거래일, 목표 만료만·+25%, 코로나(2020-02-01~2020-06-30 진입) 포함·제외 |",  # noqa: E501
             "| 시장 상태 | 신호일 SPY 종가 ≤ 252일 고점 × 0.9 → 동반 |",
             "| 한계 | 현재 상장만 — 안정배당이 이겨도 보수적 결론, 지면 판단 보류 |", ""]
    hdr = ["n", "진입일수", "미청산", "도달률", "평균ret", "중앙값",
           "승률", "평균hold", "mae중앙", "mae하위10%", "SPY초과"]
    lines += ["## A 그룹별 (코로나 포함·제외)", "",
              "|" + "|".join(["코로나", "보유", "목표", "그룹", *hdr,
                               "고유 종목"]) + "|",
              "|" + "|".join(["---"] * (4 + len(hdr) + 1)) + "|"]
    for ver, _ in vers:
        for h in hold_list:
            for label, _, _ in tp_list:
                for grp in groups:
                    g = [t for t in vtrades[(ver, h, label)]
                         if t["grp"] == grp]
                    df = _cell_df(g, axis, spy)
                    uq = len({t["ticker"] for t in g})
                    lines.append("|" + "|".join(
                        [ver, str(h), label, grp, *_cell_row(df, 0),
                         str(uq)]) + "|")
    lines += ["", "## B 종목 첫 진입만 (코로나 제외, 그룹 안에서)", "",
              "|" + "|".join(["보유", "목표", "그룹", *hdr, "고유 종목"]) + "|",
              "|" + "|".join(["---"] * (3 + len(hdr) + 1)) + "|"]
    for h, label in ((120, "+25%"), (60, "만료만")):
        for grp in groups:
            g = first_entry_per_ticker(
                [t for t in vtrades[("제외", h, label)] if t["grp"] == grp])
            df = _cell_df(g, axis, spy)
            uq = len({t["ticker"] for t in g})
            lines.append("|" + "|".join(
                [str(h), label, grp, *_cell_row(df, 0), str(uq)]) + "|")
    print("[info] univ A·B done", flush=True)
    lines += ["", "## C 시장 상태 × 그룹 (코로나 포함, 120일·+25%)", "",
              "|시장 상태|그룹|n|고유 종목|평균ret|승률|mae하위10%|",
              "|---|---|---|---|---|---|---|"]
    g_c = cells[(0.70, 120, "+25%")]
    for mkt, mlabel in ((True, "동반"), (False, "개별")):
        for grp in groups:
            g = [t for t in g_c if t["mkt"] == mkt and t["grp"] == grp]
            df = _cell_df(g, axis, spy)
            mae = pd.to_numeric(df["mae"], errors="coerce").to_numpy(dtype=float) \
                if len(df) else np.array([])
            mae = mae[np.isfinite(mae)]
            uq = len({t["ticker"] for t in g})
            lines.append("|" + "|".join(
                [mlabel, grp, str(len(g)), str(uq),
                 _pct(day_weighted_mean(df, "ret")),
                 _pct(day_win_rate(df, "ret")),
                 _pct(np.quantile(mae, 0.10)) if mae.size else "-"]) + "|")
    lines += ["", "## D 연도별 × 그룹 (코로나 포함, 120일·+25%)", "",
              "|연도|그룹|n|평균ret|", "|---|---|---|---|"]
    for grp in groups:
        years: dict[str, list[dict]] = {}
        for t in g_c:
            if t["grp"] == grp:
                years.setdefault(axis[t["entry_i"]][:4], []).append(t)
        for y in sorted(years):
            df = _cell_df(years[y], axis, spy)
            lines.append(f"|{y}|{grp}|{len(df)}|"
                         f"{_pct(day_weighted_mean(df, 'ret'))}|")
    lines.append("")
    UNIV_RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIV_RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[info] wrote {UNIV_RESULTS_PATH}", flush=True)


def main(argv=None) -> None:
    """기본(인자 없음)·보유 민감도("hold")·집중("focus")·시장 상태별("regime")·삭감("cut")·반등삭감("cutreb")·전종목("univ")·배당지급사("regime_payer")."""
    sys.stdout.reconfigure(encoding="utf-8")
    args = [] if argv is None else list(argv)
    axis = _load_axis()
    if args and args[0] == "univ":
        _run_univ(axis, *_load_data(axis, all_stocks=True))
        return
    if args and args[0] == "regime_payer":
        data, spy = _load_data(axis, all_stocks=True, drop_threshold=0.5)
        for ticker in list(data):
            a = data[ticker]
            a["elig"] = a["elig"] & payer_mask(a["stable"], a["div"])
            if not bool(a["elig"].any()):
                del data[ticker]
        print(f"[info] regime_payer tickers {len(data)}", flush=True)
        _run_regime(axis, data, spy, out_path=REGIME_PAYER_RESULTS_PATH,
                    title="# REBOUND_REGIME_PAYER — 배당 지급사 대상 시장 상태별 X + 10칸 계좌",
                    universe_note="신호일 기준 배당 안정 또는 직전 252거래일 배당 합 > 0 (SIC 67xx·없음 제외, 전일 종가 ≥ $1, 20일 평균 거래대금 ≥ $1M)")
        return
    data, spy = _load_data(axis)
    if args and args[0] == "hold":
        _run_hold(axis, data, spy)
    elif args and args[0] == "focus":
        _run_focus(axis, data, spy)
    elif args and args[0] == "regime":
        _run_regime(axis, data, spy)
    elif args and args[0] == "cut":
        _run_cut(axis, data, spy)
    elif args and args[0] == "cutreb":
        _run_cutreb(axis, data, spy)
    else:
        _run_default(axis, data, spy)


if __name__ == "__main__":
    main(sys.argv[1:])
