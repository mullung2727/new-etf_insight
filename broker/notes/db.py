"""SQLite 연결 + 스키마 초기화.

``api/duck.py``의 lazy-init 패턴을 미러한다. 단일 연결을 프로세스에서
공유하되, sqlite3는 기본적으로 스레드 간 공유를 막으므로
``check_same_thread=False``로 열고 짧은 쓰기만 수행한다(로컬 단일 사용자).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# 기본 경로는 broker/notes.db. NOTES_DB_PATH로 GDrive 폴더 등으로 옮길 수 있다.
_DEFAULT = Path(__file__).resolve().parent.parent / "notes.db"
NOTES_DB_PATH = Path(os.getenv("NOTES_DB_PATH", str(_DEFAULT))).resolve()

_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    uid            TEXT PRIMARY KEY,
    symbol         TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open',
    target_price   INTEGER,
    holding_period TEXT,
    buy_reason     TEXT,
    memo           TEXT,
    user_id        TEXT NOT NULL DEFAULT 'local',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_uid    TEXT NOT NULL REFERENCES notes(uid) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,
    price       INTEGER NOT NULL,
    qty         INTEGER NOT NULL,
    executed_at TEXT NOT NULL,
    memo        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_symbol ON notes(symbol);
CREATE INDEX IF NOT EXISTS idx_events_note_uid ON note_events(note_uid);

-- 거래 원장: broker를 통과한 모든 주문 기록. notes와 무관한 독립 테이블.
CREATE TABLE IF NOT EXISTS kiwoom_trade_history (
    order_no    TEXT PRIMARY KEY,
    date        TEXT,
    ticker      TEXT,
    side        TEXT,
    order_type  TEXT,
    qty         INTEGER,
    price       INTEGER,
    status      TEXT,
    source      TEXT,
    raw         TEXT,
    created_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_trade_history_date ON kiwoom_trade_history(date);
"""


def init() -> sqlite3.Connection:
    """연결을 열고 테이블이 없으면 생성한다. 멱등."""
    global _conn
    if _conn is not None:
        return _conn
    NOTES_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(NOTES_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    _conn = conn
    logger.info("notes SQLite ready: %s", NOTES_DB_PATH)
    return _conn


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        return init()
    return _conn
