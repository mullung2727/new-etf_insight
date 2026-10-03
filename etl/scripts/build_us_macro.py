"""Build/refresh the US macro cache (us_macro.duckdb) from the FRED API.

Stores received values together with their fetch time: a changed value for
the same (series_id, date) adds a new row instead of overwriting, so the
value visible at any past point stays reproducible via load_series(as_of=...).

Usage (from etl/):
    uv run python scripts/build_us_macro.py
    uv run python scripts/build_us_macro.py --db-path db/us_macro.duckdb
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

import argparse
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import requests
from dotenv import load_dotenv

from build_us_ohlcv import _default_download, fetch_batch

ROOT = Path(__file__).resolve().parents[2]                        # new-etf_insight/
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "us_macro.duckdb"
ENV_PATH = ROOT / ".env"

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES = (
    "BAMLH0A0HYM2",    # ICE BofA US High Yield OAS
    "BAMLH0A3HYC",     # CCC & lower OAS
    "BAMLH0A1HYBB",    # BB OAS
    "BAMLC0A0CM",      # US Corporate (IG) OAS
    "BAMLC0A4CBBB",    # BBB OAS
    "BAMLH0A0HYM2EY",  # HY effective yield
    "NFCI",            # Chicago Fed NFCI (weekly, revised)
    "STLFSI4",         # St. Louis Fed Financial Stress (weekly)
    "VIXCLS",          # CBOE VIX close (daily)
)

INDEX_TICKERS = ("^VIX", "^VIX3M")   # 신호용 지수 (매매 대상 아님 → us_ohlcv 가 아니라 여기)
INDEX_FROM_DATE = "19900101"
NEW_YORK = ZoneInfo("America/New_York")
INDEX_CLOSE_CUTOFF = time(16, 30)    # VIX 공식 종가 16:15 + 여유

_CREATE_FRED_OBS = """
CREATE TABLE IF NOT EXISTS fred_obs (
    series_id  VARCHAR,
    date       VARCHAR,     -- YYYYMMDD 관측일
    value      DOUBLE,
    fetched_at TIMESTAMP,   -- UTC, naive (tzinfo 없음)
    PRIMARY KEY (series_id, date, fetched_at)
)
"""
_CREATE_INDEX_OHLCV = """
CREATE TABLE IF NOT EXISTS index_ohlcv (
    date VARCHAR, ticker VARCHAR,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    fetched_at TIMESTAMP,
    PRIMARY KEY (date, ticker)
)
"""


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_CREATE_FRED_OBS)
    con.execute(_CREATE_INDEX_OHLCV)


def fetch_fred(
    series_id: str,
    api_key: str,
    *,
    get: Callable[..., requests.Response] = requests.get,
) -> list[tuple[str, float]]:
    """Fetch the full observation history of one FRED series.

    Returns [(YYYYMMDD, value)] in response order. Rows with value "."
    (FRED missing marker) are dropped. Errors never echo the api_key:
    request exceptions surface as their class name only.
    """
    try:
        resp = get(
            FRED_URL,
            params={"series_id": series_id, "api_key": api_key, "file_type": "json"},
            timeout=60,
        )
    except Exception as exc:
        raise RuntimeError(f"FRED {series_id} failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise RuntimeError(f"FRED {series_id} failed: HTTP {resp.status_code}")
    try:
        observations = resp.json()["observations"]
    except Exception as exc:
        raise RuntimeError(f"FRED {series_id} failed: {type(exc).__name__}") from None
    rows: list[tuple[str, float]] = []
    for obs in observations:
        if obs.get("value") == ".":
            continue
        rows.append((str(obs["date"]).replace("-", ""), float(obs["value"])))
    return rows


def upsert_fred(
    con: duckdb.DuckDBPyConnection,
    series_id: str,
    rows: list[tuple[str, float]],
    fetched_at: datetime,
) -> int:
    """Insert only new or changed observations; return the inserted row count.

    Compares each (date, value) against the stored latest row (max fetched_at)
    for that (series_id, date). Unchanged values are skipped, and observation
    dates missing from this response (3-year rolling window drop-off) are kept.
    One transaction per series.
    """
    latest: dict[str, tuple[float, datetime]] = {}
    for date, value, ts in con.execute(
        "SELECT date, value, fetched_at FROM fred_obs WHERE series_id = ?",
        [series_id],
    ).fetchall():
        if date not in latest or ts > latest[date][1]:
            latest[date] = (value, ts)
    deduped = {date: value for date, value in rows}  # 입력 내 중복 관측일은 마지막 값
    new = [
        (series_id, date, value, fetched_at)
        for date, value in deduped.items()
        if date not in latest or latest[date][0] != value
    ]
    if not new:
        return 0
    con.execute("BEGIN TRANSACTION")
    try:
        con.executemany("INSERT INTO fred_obs VALUES (?,?,?,?)", new)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(new)


def load_series(
    con: duckdb.DuckDBPyConnection,
    series_id: str,
    as_of: datetime | None = None,
) -> list[tuple[str, float]]:
    """Latest value per observation date, ascending. as_of limits to fetched_at <= as_of."""
    if as_of is None:
        stored = con.execute(
            "SELECT date, arg_max(value, fetched_at) FROM fred_obs"
            " WHERE series_id = ? GROUP BY date ORDER BY date",
            [series_id],
        ).fetchall()
    else:
        stored = con.execute(
            "SELECT date, arg_max(value, fetched_at) FROM fred_obs"
            " WHERE series_id = ? AND fetched_at <= ? GROUP BY date ORDER BY date",
            [series_id, as_of],
        ).fetchall()
    return [(date, float(value)) for date, value in stored]


def index_cutoff_day(now_ny: datetime) -> str:
    """이 날짜 이상인 봉은 버린다. 마감 시각 이후면 다음 날, 아니면 오늘."""
    if now_ny.time() >= INDEX_CLOSE_CUTOFF:
        return (now_ny.date() + timedelta(days=1)).strftime("%Y%m%d")
    return now_ny.date().strftime("%Y%m%d")


def replace_index(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    frame: pd.DataFrame,
    cutoff_day: str,
    fetched_at: datetime,
) -> int:
    """티커 전 구간을 한 트랜잭션으로 교체하고 넣은 행 수를 반환한다.

    잘린 응답(빈 응답이거나 새 첫 날짜가 저장된 첫 날짜보다 늦음)이면
    RuntimeError 로 기존 행을 보존한다.
    """
    rows: list[tuple] = []
    for idx, values in frame.iterrows():
        day = pd.Timestamp(idx).strftime("%Y%m%d")
        if day >= cutoff_day:
            continue
        ohlc = [values.get(key) for key in ("Open", "High", "Low", "Close")]
        if any(pd.isna(value) for value in ohlc):
            continue
        rows.append(
            (
                day, ticker,
                float(ohlc[0]), float(ohlc[1]), float(ohlc[2]), float(ohlc[3]),
                fetched_at,
            )
        )
    con.execute("BEGIN TRANSACTION")
    try:
        stored_first = con.execute(
            "SELECT min(date) FROM index_ohlcv WHERE ticker=?", [ticker]
        ).fetchone()[0]
        first = min((row[0] for row in rows), default=None)
        if first is None or (stored_first is not None and first > stored_first):
            raise RuntimeError(
                f"{ticker} 지수 재조회가 기존 구간을 못 덮는다 (저장 {stored_first} < 응답 {first})"
            )
        con.execute("DELETE FROM index_ohlcv WHERE ticker=?", [ticker])
        con.executemany("INSERT INTO index_ohlcv VALUES (?,?,?,?,?,?,?)", rows)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)


def ensure_index(
    con: duckdb.DuckDBPyConnection,
    *,
    now_ny: datetime | None = None,
    fetched_at: datetime | None = None,
    download: Callable[[list[str], str, str], pd.DataFrame] = _default_download,
    tickers: tuple[str, ...] = INDEX_TICKERS,
) -> dict:
    """지수 일봉 전 구간을 받아 티커별로 교체한다. 한 티커 실패가 나머지를 막지 않는다."""
    now_ny = now_ny or datetime.now(NEW_YORK)
    fetched_at = fetched_at or datetime.now(timezone.utc).replace(tzinfo=None)
    end = (now_ny.date() + timedelta(days=1)).strftime("%Y%m%d")
    cutoff_day = index_cutoff_day(now_ny)
    ticker_list = list(tickers)
    try:
        frames, missing = fetch_batch(ticker_list, INDEX_FROM_DATE, end, download)
    except Exception as exc:
        return {"inserted": {}, "failed": {ticker: type(exc).__name__ for ticker in ticker_list}}
    inserted: dict[str, int] = {}
    failed: dict[str, str] = {ticker: "missing from download response" for ticker in missing}
    for ticker, frame in frames.items():
        try:
            inserted[ticker] = replace_index(con, ticker, frame, cutoff_day, fetched_at)
        except Exception as exc:
            failed[ticker] = str(exc)
    return {"inserted": inserted, "failed": failed}


def run(
    con: duckdb.DuckDBPyConnection,
    api_key: str,
    *,
    series: tuple[str, ...] = FRED_SERIES,
    fetch: Callable[[str, str], list[tuple[str, float]]] = fetch_fred,
    now: datetime | None = None,
    index_download: Callable[[list[str], str, str], pd.DataFrame] = _default_download,
    now_ny: datetime | None = None,
    indices: tuple[str, ...] = INDEX_TICKERS,
) -> dict:
    """Fetch every series and upsert; one series failing does not stop the rest."""
    fetched_at = now or datetime.now(timezone.utc).replace(tzinfo=None)
    inserted: dict[str, int] = {}
    failed: dict[str, str] = {}
    for series_id in series:
        try:
            rows = fetch(series_id, api_key)
            inserted[series_id] = upsert_fred(con, series_id, rows, fetched_at)
        except Exception as exc:
            failed[series_id] = str(exc)
    if indices:
        try:
            index_result = ensure_index(
                con, now_ny=now_ny, fetched_at=fetched_at,
                download=index_download, tickers=indices,
            )
            inserted.update(index_result["inserted"])
            failed.update(index_result["failed"])
        except Exception as exc:
            for ticker in indices:
                failed.setdefault(ticker, str(exc))
    return {"inserted": inserted, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/refresh the US macro cache from FRED")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise SystemExit(f"error: FRED_API_KEY not set (checked environment and {ENV_PATH})")

    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.db_path))
    try:
        ensure_schema(con)
        result = run(con, api_key)
    finally:
        con.close()

    for series_id, count in result["inserted"].items():
        print(f"{series_id}={count}")
    for series_id, error in result["failed"].items():
        print(f"{series_id} FAILED: {error}")
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
