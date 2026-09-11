"""1분봉 DuckDB 저장소 — 봉 단위로 저장하고 없는 날짜만 키움에서 채운다.

기존 minute_bar_cache 는 `(종목, 기준일)` 키의 JSON 파일이라
  - 같은 종목·같은 날 봉이 기준일마다 중복 저장되고
  - 보유일수(horizon)를 바꾸면 기준일이 밀려 이미 가진 데이터를 다시 조회했다.

여기서는 `(종목, scope, timestamp)` 를 PK 로 봉만 저장하고,
어느 날짜를 조회 완료했는지는 minute_fetched 로 따로 표시한다.
표시를 따로 두는 이유: 거래정지 등으로 봉이 0개인 날과 아직 안 받은 날을 구분해야 하기 때문.

Usage (repo root):
    etl\\.venv\\Scripts\\python.exe -m research.watchlist_expected_return.minute_bar_store --migrate
"""
from __future__ import annotations

import argparse
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import duckdb

from research.watchlist_expected_return.minute_bar_cache import (
    DEFAULT_CACHE_DIR,
    normalize_minute_bar,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "etl" / "db" / "minute_bars.duckdb"
DEFAULT_SCOPE = "1"
SESSION_OPEN, SESSION_CLOSE = "090000", "153000"
MAX_PAGES = 5
WINDOW_DAYS = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
  ticker VARCHAR, scope VARCHAR, timestamp VARCHAR,
  date VARCHAR, time VARCHAR,
  open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,
  PRIMARY KEY (ticker, scope, timestamp)
);
CREATE TABLE IF NOT EXISTS minute_fetched (
  ticker VARCHAR, scope VARCHAR, date VARCHAR,
  status VARCHAR DEFAULT 'complete',
  PRIMARY KEY (ticker, scope, date)
);
"""

_COLUMNS = ("ticker", "scope", "timestamp", "date", "time",
            "open", "high", "low", "close", "volume")


class MinuteFetchIncomplete(RuntimeError):
    """요청 범위의 장 시작까지 도달하지 못한 분봉 조회."""


def _ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SCHEMA)
    columns = {row[1] for row in con.execute("PRAGMA table_info('minute_fetched')").fetchall()}
    if "status" not in columns:
        con.execute("ALTER TABLE minute_fetched ADD COLUMN status VARCHAR DEFAULT 'complete'")
        con.execute(
            """
            UPDATE minute_fetched AS fetched
            SET status = 'empty'
            WHERE NOT EXISTS (
                SELECT 1 FROM minute_bars AS bars
                WHERE bars.ticker = fetched.ticker
                  AND bars.scope = fetched.scope
                  AND bars.date = fetched.date
            )
            """
        )


@contextmanager
def connect(
    db_path: Path = DEFAULT_DB_PATH, read_only: bool = False, *, lock_timeout: float = 30.0
) -> Iterator[duckdb.DuckDBPyConnection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + lock_timeout
    while True:
        try:
            con = duckdb.connect(str(db_path), read_only=read_only)
            threads = os.getenv("MINUTE_DB_THREADS")
            memory_limit = os.getenv("MINUTE_DB_MEMORY_LIMIT")
            if threads:
                con.execute(f"SET threads={int(threads)}")
            if memory_limit:
                con.execute("SET memory_limit=?", [memory_limit])
            break
        except duckdb.IOException as exc:
            message = str(exc).lower()
            locked = any(s in message for s in (
                "could not set lock", "conflicting lock", "used by another process",
                "being used", "다른 프로세스",
            ))
            if not locked:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"분봉 DB 사용 중: {db_path} ({lock_timeout}초 대기)") from exc
            time.sleep(min(1.0, remaining))
    try:
        if not read_only:
            _ensure_schema(con)
        yield con
    finally:
        con.close()


def _insert_bars(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    """PK 충돌은 무시. 임시테이블 경유 — PK 인덱스에 직접 executemany 하면 느리다."""
    if not rows:
        return 0
    con.execute("CREATE OR REPLACE TEMP TABLE _staging AS SELECT * FROM minute_bars LIMIT 0")
    con.executemany(f"INSERT INTO _staging VALUES ({', '.join('?' * len(_COLUMNS))})", rows)
    return len(con.execute(
        "INSERT INTO minute_bars SELECT DISTINCT * FROM _staging "
        "ON CONFLICT DO NOTHING RETURNING timestamp"
    ).fetchall())


def _mark_fetched(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    scope: str,
    dates: list[str],
    empty_dates: set[str],
) -> None:
    con.executemany(
        "INSERT INTO minute_fetched (ticker, scope, date, status) VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [(ticker, scope, date, "empty" if date in empty_dates else "complete") for date in dates],
    )


def missing_dates(
    con: duckdb.DuckDBPyConnection, ticker: str, dates: list[str], scope: str = DEFAULT_SCOPE
) -> list[str]:
    if not dates:
        return []
    placeholders = ", ".join("?" * len(dates))
    known = {
        row[0] for row in con.execute(
            f"SELECT date FROM minute_fetched WHERE ticker = ? AND scope = ? "
            f"AND date IN ({placeholders})",
            [ticker, scope, *dates],
        ).fetchall()
    }
    return [date for date in dates if date not in known]


def fetch_into_store(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    dates: list[str],
    *,
    scope: str = DEFAULT_SCOPE,
    fetch_page: Callable[..., dict[str, Any]] | None = None,
    max_pages: int = MAX_PAGES,
) -> int:
    """dates 중 미조회 구간만 키움에서 받아 적재한다. 적재한 봉 수를 반환."""
    pending = sorted(set(missing_dates(con, ticker, dates, scope)), reverse=True)
    if not pending:
        return 0
    # 희소한 요청 날짜도 달력 기준으로 분할하여 긴 중간 구간을 조회하지 않는다.
    windows: list[list[str]] = []
    for date in pending:
        if not windows or (
            datetime.strptime(windows[-1][0], "%Y%m%d") - datetime.strptime(date, "%Y%m%d")
        ).days >= WINDOW_DAYS:
            windows.append([])
        windows[-1].append(date)
    if len(windows) > 1:
        return sum(fetch_into_store(
            con, ticker, window, scope=scope, fetch_page=fetch_page, max_pages=max_pages
        ) for window in windows)
    if fetch_page is None:
        from broker.kiwoom.quotes import get_minute_chart

        fetch_page = get_minute_chart

    base_dt, earliest_dt = max(pending), min(pending)
    bars_by_time: dict[str, dict[str, Any]] = {}
    cont_yn, next_key = "N", ""
    exhausted_history = False
    for _ in range(max_pages):
        result = fetch_page(ticker, scope, base_dt, cont_yn=cont_yn, next_key=next_key)
        for raw in result["bars"]:
            bar = normalize_minute_bar(raw)
            if bar:
                bars_by_time[bar["timestamp"]] = bar
        if _reached_session_start(bars_by_time.values(), earliest_dt):
            break
        cont_yn, next_key = result["cont_yn"], result["next_key"]
        if cont_yn != "Y" or not next_key:
            exhausted_history = True
            break

    covered = [bar for bar in bars_by_time.values() if bar["date"] in pending]
    inserted = _insert_bars(con, [
        (ticker, scope, bar["timestamp"], bar["date"], bar["time"],
         bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"])
        for bar in covered
    ])
    # 정상 응답이 continuation 없이 끝났다면 해당 기준일 이전의 가용 이력을 모두 본 것이다.
    # 봉이 전혀 없거나 첫 요청일만 무거래여도 완료로 기록해야 같은 날짜를 실패로 재시도하지 않는다.
    complete = _reached_session_start(bars_by_time.values(), earliest_dt) or exhausted_history
    if complete:
        # earliest_dt 장 시작까지 닿았으면 그 사이 날짜는 봉이 0개여도 조회 완료다(거래정지 등).
        fetched_dates = [d for d in pending if earliest_dt <= d <= base_dt]
        bar_dates = {bar["date"] for bar in covered}
        _mark_fetched(con, ticker, scope, fetched_dates, empty_dates=set(fetched_dates) - bar_dates)
    else:
        raise MinuteFetchIncomplete(
            f"{ticker} {earliest_dt}~{base_dt}: {max_pages}페이지 안에 장 시작까지 도달하지 못함"
        )
    return inserted


def _reached_session_start(bars: Any, earliest_dt: str) -> bool:
    bars = list(bars)
    earliest_day = sorted(
        (bar for bar in bars if bar["date"] == earliest_dt), key=lambda item: item["time"]
    )
    return bool(
        any(bar["date"] < earliest_dt for bar in bars)
        or (earliest_day and earliest_day[0]["time"] <= SESSION_OPEN)
    )


def load_bars(
    con: duckdb.DuckDBPyConnection,
    ticker: str,
    dates: list[str],
    *,
    scope: str = DEFAULT_SCOPE,
    fetch_page: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """dates 의 정규장 1분봉. 미조회 날짜는 받아서 적재한 뒤 함께 반환한다."""
    if not dates:
        return []
    fetch_into_store(con, ticker, dates, scope=scope, fetch_page=fetch_page)
    placeholders = ", ".join("?" * len(dates))
    rows = con.execute(
        f"SELECT timestamp, date, time, open, high, low, close, volume FROM minute_bars "
        f"WHERE ticker = ? AND scope = ? AND date IN ({placeholders}) "
        f"AND time BETWEEN ? AND ? ORDER BY timestamp",
        [ticker, scope, *dates, SESSION_OPEN, SESSION_CLOSE],
    ).fetchall()
    return [dict(zip(("timestamp", "date", "time", "open", "high", "low", "close", "volume"), row))
            for row in rows]


def migrate_json_cache(
    con: duckdb.DuckDBPyConnection, cache_dir: Path = DEFAULT_CACHE_DIR, scope: str = DEFAULT_SCOPE
) -> dict[str, int]:
    """기존 JSON 캐시를 통째로 적재한다. earliest_requested_dt~base_dt 만 조회완료로 표시."""
    pattern = str(cache_dir / f"*_{scope}m.json").replace("\\", "/")
    con.execute("CREATE OR REPLACE TEMP TABLE _cache AS "
                "SELECT * FROM read_json_auto(?, union_by_name=true)", [pattern])
    files = con.execute("SELECT count(*) FROM _cache").fetchone()[0]
    before = con.execute("SELECT count(*) FROM minute_bars").fetchone()[0]
    con.execute(
        """
        INSERT INTO minute_bars
        SELECT symbol, ?, b.timestamp, b.date, b.time,
               b.open, b.high, b.low, b.close, b.volume
        FROM _cache, UNNEST(bars) AS u(b)
        WHERE b.date BETWEEN _cache.earliest_requested_dt AND _cache.base_dt
        ON CONFLICT DO NOTHING
        """,
        [scope],
    )
    inserted = con.execute("SELECT count(*) FROM minute_bars").fetchone()[0] - before
    con.execute(
        """
        INSERT INTO minute_fetched (ticker, scope, date, status)
        SELECT DISTINCT symbol, ?, b.date, 'complete'
        FROM _cache, UNNEST(bars) AS u(b)
        WHERE _cache.complete = true
          AND b.date BETWEEN _cache.earliest_requested_dt AND _cache.base_dt
        ON CONFLICT DO NOTHING
        """,
        [scope],
    )
    return {
        "files": files,
        "bars_inserted": inserted,
        "bars_total": con.execute("SELECT count(*) FROM minute_bars").fetchone()[0],
        "fetched_days": con.execute("SELECT count(*) FROM minute_fetched").fetchone()[0],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="1분봉 DuckDB 저장소")
    parser.add_argument("--migrate", action="store_true", help="기존 JSON 캐시를 적재")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args(argv)

    with connect(args.db) as con:
        if args.migrate:
            print(migrate_json_cache(con, args.cache_dir))
        else:
            print({
                "bars": con.execute("SELECT count(*) FROM minute_bars").fetchone()[0],
                "tickers": con.execute("SELECT count(DISTINCT ticker) FROM minute_bars").fetchone()[0],
                "fetched_days": con.execute("SELECT count(*) FROM minute_fetched").fetchone()[0],
            })


if __name__ == "__main__":
    main()
