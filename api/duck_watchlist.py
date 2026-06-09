"""Per-request DuckDB access for the watchlist batch outputs.

Unlike ``duck.py`` (a long-lived shared read-only connection to
``etf_insight.duckdb``), the watchlist/OHLCV databases are written by the
``build_watchlist.py`` batch. DuckDB allows either a single read-write process
or many read-only processes — a persistent read-only handle here would block
the batch's read-write connect. So each request opens its own read-only
connection and closes it immediately, keeping the lock window tiny.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Iterator

import duckdb

# Defaults assume api/ and etl/ are siblings under the repo root (same as duck.py).
WATCHLIST_DB_PATH = Path(
    os.getenv("WATCHLIST_DB_PATH", "../etl/db/watchlist.duckdb")
).resolve()
KRX_DB_PATH = Path(
    os.getenv("KRX_DB_PATH", "../etl/db/krx_ohlcv.duckdb")
).resolve()


@contextmanager
def _read_cursor(db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    if not db_path.exists():
        raise FileNotFoundError(f"DuckDB file not found: {db_path}")
    con = duckdb.connect(database=str(db_path), read_only=True)
    try:
        yield con
    finally:
        con.close()


def watchlist_cursor() -> ContextManager[duckdb.DuckDBPyConnection]:
    return _read_cursor(WATCHLIST_DB_PATH)


def krx_cursor() -> ContextManager[duckdb.DuckDBPyConnection]:
    return _read_cursor(KRX_DB_PATH)
