"""Per-request DuckDB access for ETF insight data."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

DUCKDB_PATH = Path(
    os.getenv("DUCKDB_PATH", "../etl/db/etf_insight.duckdb")
).resolve()


@contextmanager
def read_cursor() -> Iterator[duckdb.DuckDBPyConnection]:
    if not DUCKDB_PATH.exists():
        raise FileNotFoundError(f"DuckDB file not found: {DUCKDB_PATH}")
    con = duckdb.connect(database=str(DUCKDB_PATH), read_only=True)
    try:
        yield con
    finally:
        con.close()
