"""watchlist.sqlite3 공용 커넥션 헬퍼 (SQLite).

watchlist DB(watchlist/llm_scores/intraday_ranking/close_bet_orders)는 5개 배치
스크립트가 공유한다. krx_ohlcv 는 DuckDB 유지 — 이 헬퍼는 watchlist DB 전용.

- ``connect_rw``: writer. WAL 모드(reader-writer 동시성). with 블록 정상 종료 시 commit
  (SQLite 는 DuckDB 와 달리 자동 커밋이 아니다). 항상 close.
- ``connect_ro``: reader. ``mode=ro`` URI 대신 ``PRAGMA query_only=ON`` 사용
  (read-only 프로세스는 WAL 의 -shm 공유메모리를 못 열어 실패하기 때문).

DuckDB 의 ``with duckdb.connect(...) as con`` 은 블록 종료 시 close 하지만,
``with sqlite3.connect(...) as con`` 은 commit 만 하고 close 하지 않는다(파일 락 잔존).
그래서 명시적 close 를 보장하는 이 contextmanager 로 통일한다.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def connect_rw(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextmanager
def connect_ro(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()
