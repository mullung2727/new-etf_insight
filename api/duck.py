"""DuckDB connection module.

A single read-only connection is shared across the process. DuckDB's Python
client is thread-safe for read queries when each thread uses its own cursor,
so handlers acquire a cursor via ``get_cursor`` rather than the raw connection.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

DUCKDB_PATH = Path(
    os.getenv("DUCKDB_PATH", "../etl/db/etf_insight.duckdb")
).resolve()

_conn: duckdb.DuckDBPyConnection | None = None


def init_connection() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is not None:
        return _conn
    if not DUCKDB_PATH.exists():
        raise FileNotFoundError(f"DuckDB file not found: {DUCKDB_PATH}")
    _conn = duckdb.connect(database=str(DUCKDB_PATH), read_only=True)
    logger.info("DuckDB connected: %s", DUCKDB_PATH)
    return _conn


def get_cursor() -> duckdb.DuckDBPyConnection:
    if _conn is None:
        init_connection()
    assert _conn is not None
    return _conn.cursor()
