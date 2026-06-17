"""Per-request SQLite access for ETF insight data.

etf_insight 는 SQLite(`etf_insight.sqlite3`). writer(build_db.py)가 WAL 모드로
쓰므로, reader 는 `mode=ro` URI 대신 일반 connect + `PRAGMA query_only=ON` 을 쓴다.
(read-only 프로세스는 WAL 의 -shm 공유메모리를 못 열어 "unable to open database file"
이 나기 때문 — query_only 는 같은 효과로 쓰기만 차단하면서 WAL 을 정상 연다.)

커서를 yield 한다: sqlite3 는 Connection 에 fetchall/description 이 없고 Cursor 에만
있으므로, 라우터의 `cur.execute(...).fetchall()` / `rows_to_dicts(cur)` 패턴을
그대로 유지하려면 커서를 넘겨야 한다.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DUCKDB_PATH = Path(
    os.getenv("DUCKDB_PATH", "../etl/db/etf_insight.sqlite3")
).resolve()


@contextmanager
def read_cursor() -> Iterator[sqlite3.Cursor]:
    if not DUCKDB_PATH.exists():
        raise FileNotFoundError(f"SQLite file not found: {DUCKDB_PATH}")
    con = sqlite3.connect(str(DUCKDB_PATH))
    con.execute("PRAGMA query_only=ON")
    try:
        yield con.cursor()
    finally:
        con.close()
