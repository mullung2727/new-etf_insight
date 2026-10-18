from __future__ import annotations

import re
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
from fastapi import APIRouter, Query

import duck_watchlist
from duck import read_cursor
from schemas import DailyHolding, HoldingEvent, HoldingPoint
from sql_utils import rows_to_dicts

_PROJECT_DIR = Path(__file__).resolve().parents[2]
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))
from research.backtest_daily.adjust import adj_price, adj_returns  # noqa: E402

router = APIRouter(tags=["holdings"])

RETURN_MONTHS = (1, 3, 6, 12)

_KR_RE = re.compile(r"^\d{6}$")
_US_SUFFIX = " US EQUITY"


def _flags(row: dict) -> dict:
    w = row.get("weight_pct") or 0.0
    row["is_conviction"] = w >= 5.0
    row["is_faded"] = w <= 3.0
    return row


def _base_weights(cur, etf: str, where_sql: str, latest: str) -> dict | None:
    cur.execute(
        f"SELECT MAX(asof) FROM etf_holdings_daily WHERE etf = ? AND {where_sql}",
        [etf, latest],
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    cur.execute(
        "SELECT code, weight_pct FROM etf_holdings_daily WHERE etf = ? AND asof = ?",
        [etf, row[0]],
    )
    return {r[0]: r[1] or 0.0 for r in cur.fetchall()}


def _months_before(asof: str, n: int) -> str:
    """YYYY-MM-DD 에서 n개월 전 같은 날(말일 넘으면 그 달 말일), YYYYMMDD 로."""
    y, m, d = int(asof[:4]), int(asof[5:7]), int(asof[8:10])
    m -= n
    while m <= 0:
        m += 12
        y -= 1
    return f"{y:04d}{m:02d}{min(d, monthrange(y, m)[1]):02d}"


def _period_start(asof: str) -> str:
    """12개월 컷오프의 달력 20일(약 10거래일 여유) 이전, YYYYMMDD."""
    c = _months_before(asof, 12)
    d = date(int(c[:4]), int(c[4:6]), int(c[6:8])) - timedelta(days=20)
    return d.strftime("%Y%m%d")


def _us_ticker(code: str) -> str | None:
    if code.endswith(_US_SUFFIX):
        return code[: -len(_US_SUFFIX)].replace("/", "-")
    return None


def _window_returns(
    px: pd.DataFrame, value_col: str, asof: str
) -> dict[str, dict[str, float | None]]:
    """종목별 끝값(asof 이하 마지막 행) vs n개월 컷오프(이하 마지막 행) 수익률(%)."""
    end = asof.replace("-", "")
    cutoffs = {n: _months_before(asof, n) for n in RETURN_MONTHS}
    out: dict[str, dict[str, float | None]] = {}
    for ticker, grp in px.groupby("ticker", sort=False):
        past = grp[grp["date"] <= end]
        if past.empty:
            continue
        end_v = float(past[value_col].iloc[-1])
        row: dict[str, float | None] = {}
        for n in RETURN_MONTHS:
            base = grp[grp["date"] <= cutoffs[n]]
            if base.empty:
                row[f"r{n}m"] = None
                continue
            base_v = float(base[value_col].iloc[-1])
            row[f"r{n}m"] = round((end_v / base_v - 1) * 100, 2) if base_v else None
        out[ticker] = row
    return out


def _kr_returns(codes: list[str], asof: str) -> dict[str, dict[str, float | None]]:
    if not codes:
        return {}
    start, end = _period_start(asof), asof.replace("-", "")
    try:
        with duck_watchlist.krx_cursor() as con:
            px = con.execute(
                "WITH mkt AS (SELECT DISTINCT date FROM ohlcv WHERE date >= ? AND date <= ?), "
                "m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS ms FROM mkt) "
                "SELECT o.ticker, o.date, o.close, o.cmp_prev, m.ms "
                "FROM ohlcv o JOIN m ON o.date = m.date "
                f"WHERE o.ticker IN ({','.join('?' for _ in codes)}) "
                "AND o.volume > 0 AND o.open > 0 AND o.close > 0 "
                "ORDER BY o.ticker, o.date",
                [start, end, *codes],
            ).df()
    except (duckdb.Error, FileNotFoundError):
        return {}
    if px.empty:
        return {}
    px["adj"] = adj_price(px["ticker"].to_numpy(), adj_returns(px, use_krx_reference=True))
    return _window_returns(px, "adj", asof)


def _us_returns(tickers: list[str], asof: str) -> dict[str, dict[str, float | None]]:
    # ponytail: close 가 split-adjusted 라 비율 그대로. 새 분할 뒤 과거 행 재수집 안 되면 그 종목만 틀림 → splits 표로 보정 추가.
    if not tickers:
        return {}
    start, end = _period_start(asof), asof.replace("-", "")
    try:
        with duck_watchlist.us_cursor() as con:
            px = con.execute(
                "SELECT ticker, date, close FROM ohlcv "
                f"WHERE ticker IN ({','.join('?' for _ in tickers)}) "
                "AND date >= ? AND date <= ? AND close > 0 "
                "ORDER BY ticker, date",
                [*tickers, start, end],
            ).df()
    except (duckdb.Error, FileNotFoundError):
        return {}
    if px.empty:
        return {}
    return _window_returns(px, "close", asof)


@router.get(
    "/holdings/latest",
    response_model=list[DailyHolding],
    operation_id="get_latest_holdings",
    summary="Latest holdings snapshot for a TIME ETF",
    description=(
        "Returns the most recent etf_holdings_daily rows for the given ETF code "
        "(426030/385720/495060/456600), highest weight first. is_conviction "
        "(w>=5) and is_faded (w<=3) flags drive the UI highlight. d1/d7/d30 are weight changes (%p) vs the previous, 7-day-ago (or nearest earlier), and 30-day-ago (or nearest earlier) snapshots; None when no earlier snapshot exists. "
        "r1m/r3m/r6m/r12m are the holding's own price returns (%) vs the nearest trading day at/before 1/3/6/12 months ago (KR split-adjusted via adj_returns, US split-adjusted closes); None when no earlier price exists."
    ),
)
def get_latest_holdings(
    etf: str = Query(..., description="ETF code"),
) -> list[DailyHolding]:
    with read_cursor() as cur:
        cur.execute("SELECT MAX(asof) FROM etf_holdings_daily WHERE etf = ?", [etf])
        row = cur.fetchone()
        if not row or not row[0]:
            return []
        latest = row[0]
        base_d1 = _base_weights(cur, etf, "asof < ?", latest)
        base_d7 = _base_weights(cur, etf, "asof <= date(?, '-7 days')", latest)
        base_d30 = _base_weights(cur, etf, "asof <= date(?, '-30 days')", latest)
        cur.execute(
            "SELECT asof, etf, code, name, qty, amt_krw, weight_pct "
            "FROM etf_holdings_daily WHERE etf = ? AND asof = ? ORDER BY weight_pct DESC",
            [etf, latest],
        )
        rows = rows_to_dicts(cur)
    for r in rows:
        w = r.get("weight_pct") or 0.0
        rcode = r.get("code")
        r["d1"] = round(w - base_d1.get(rcode, 0.0), 2) if base_d1 is not None else None
        r["d7"] = round(w - base_d7.get(rcode, 0.0), 2) if base_d7 is not None else None
        r["d30"] = round(w - base_d30.get(rcode, 0.0), 2) if base_d30 is not None else None
    kr_codes = sorted({r["code"] for r in rows if r.get("code") and _KR_RE.match(r["code"])})
    us_map: dict[str, str] = {}
    for r in rows:
        code = r.get("code") or ""
        t = _us_ticker(code)
        if t:
            us_map[code] = t
    kr_ret = _kr_returns(kr_codes, latest)
    us_ret = _us_returns(sorted(set(us_map.values())), latest)
    for r in rows:
        code = r.get("code") or ""
        if code in kr_ret:
            ret = kr_ret[code]
        elif code in us_map:
            ret = us_ret.get(us_map[code], {})
        else:
            ret = {}
        for n in RETURN_MONTHS:
            r[f"r{n}m"] = ret.get(f"r{n}m")
    return [DailyHolding.model_validate(_flags(r)) for r in rows]


@router.get(
    "/holdings/events",
    response_model=list[HoldingEvent],
    operation_id="list_holding_events",
    summary="Holding change events (entries/exits/crossings)",
    description=(
        "Returns etf_holding_events rows, newest first. kind is ENTRY (new), "
        "EXIT (removed), UP5 (crossed above 5%) or DN3 (fell below 3%)."
    ),
)
def list_holding_events(
    etf: str | None = Query(None, description="ETF code filter"),
    days: int = Query(30, ge=1, le=365, description="lookback window in days"),
    limit: int = Query(200, ge=1, le=2000, description="max rows"),
) -> list[HoldingEvent]:
    with read_cursor() as cur:
        cur.execute(
            "SELECT date, etf, code, name, kind, w_prev, w_cur FROM etf_holding_events "
            "WHERE (? IS NULL OR etf = ?) AND date >= date('now', 'localtime', ?) "
            "ORDER BY date DESC, etf, kind LIMIT ?",
            [etf, etf, f"-{days} days", limit],
        )
        rows = rows_to_dicts(cur)
    return [HoldingEvent.model_validate(r) for r in rows]


@router.get(
    "/holdings/history",
    response_model=list[HoldingPoint],
    operation_id="get_holding_history",
    summary="Weight history for one holding",
    description="Returns (asof, weight_pct) ascending for the given ETF + code.",
)
def get_holding_history(
    etf: str = Query(..., description="ETF code"),
    code: str = Query(..., description="holding code"),
) -> list[HoldingPoint]:
    with read_cursor() as cur:
        cur.execute(
            "SELECT asof, weight_pct FROM etf_holdings_daily "
            "WHERE etf = ? AND code = ? ORDER BY asof",
            [etf, code],
        )
        rows = rows_to_dicts(cur)
    return [HoldingPoint.model_validate(r) for r in rows]
