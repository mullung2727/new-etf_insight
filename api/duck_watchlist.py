"""Per-request access for the watchlist batch outputs.

watchlist/llm_scores 는 SQLite(`watchlist.sqlite3`, build_watchlist 등 배치가 WAL 로 씀).
ohlcv(krx)는 DuckDB(`krx_ohlcv.duckdb`) 유지 — OLAP 집계 우위 때문.

watchlist reader 는 `mode=ro` URI 대신 `PRAGMA query_only=ON` 을 쓴다(WAL DB 를
read-only 프로세스가 못 여는 문제 회피, duck.py 와 동일). krx reader 는 DuckDB 의
다중 read-only 연결을 매 요청마다 열고 닫는다(배치의 read-write connect 을 막지
않도록 짧은 락 윈도우 유지).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Iterator

import duckdb

API_DIR = Path(__file__).resolve().parent
PROJECT_DIR = API_DIR.parent
ETL_DB_DIR = PROJECT_DIR / "etl" / "db"


def _resolve_project_path(value: str | None, default: Path) -> Path:
    path = Path(value) if value else default
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()

WATCHLIST_DB_PATH = _resolve_project_path(
    os.getenv("WATCHLIST_DB_PATH"), ETL_DB_DIR / "watchlist.sqlite3"
)
KRX_DB_PATH = _resolve_project_path(
    os.getenv("KRX_DB_PATH"), ETL_DB_DIR / "krx_ohlcv.duckdb"
)


@contextmanager
def watchlist_cursor() -> Iterator[sqlite3.Connection]:
    if not WATCHLIST_DB_PATH.exists():
        raise FileNotFoundError(f"SQLite file not found: {WATCHLIST_DB_PATH}")
    con = sqlite3.connect(str(WATCHLIST_DB_PATH))
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()


@contextmanager
def _krx_cursor(db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB file not found: {db_path}")
    con = duckdb.connect(database=str(db_path), read_only=True)
    try:
        yield con
    finally:
        con.close()


def krx_cursor() -> ContextManager[duckdb.DuckDBPyConnection]:
    return _krx_cursor(KRX_DB_PATH)
