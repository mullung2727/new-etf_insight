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
    name           TEXT,
    status         TEXT NOT NULL DEFAULT 'idea',
    target_price   INTEGER,
    entry_price    INTEGER,
    alert_off      INTEGER NOT NULL DEFAULT 0,
    alerted_on     TEXT,
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
    order_no    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_symbol ON notes(symbol);
CREATE INDEX IF NOT EXISTS idx_events_note_uid ON note_events(note_uid);
-- idx_events_order_no(자동연결 멱등 키)는 _migrate에서 생성 — 구버전 DB의
-- note_events에는 order_no 컬럼이 없어 ALTER 이후에 만들어야 하기 때문.

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


def _migrate(conn: sqlite3.Connection) -> None:
    """기존 DB를 현재 스키마로 끌어올린다. 멱등.

    ``CREATE TABLE IF NOT EXISTS``는 이미 존재하는 테이블에 새 컬럼을 추가하지
    않으므로, 누락 컬럼만 ALTER로 보강한다. 컬럼 추가 후 부분 유니크 인덱스도
    (스키마에 이미 있지만 구버전 DB 대비) 보장한다.
    """
    note_cols = {row["name"] for row in conn.execute("PRAGMA table_info(notes)")}
    if "name" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN name TEXT")
    if "entry_price" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN entry_price INTEGER")
        conn.execute("ALTER TABLE notes ADD COLUMN alert_off INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE notes ADD COLUMN alerted_on TEXT")
        # idea 도입 전 DB에는 체결 이벤트가 하나도 없는 open 노트가 있다 —
        # 실제로는 매수 전 조사 단계였으므로 idea로 내린다. 컬럼을 막 추가한
        # 이번 한 번만 실행(사용자가 이후 open으로 되돌린 걸 되돌리지 않도록).
        conn.execute(
            "UPDATE notes SET status = 'idea' WHERE status = 'open' "
            "AND uid NOT IN (SELECT DISTINCT note_uid FROM note_events)"
        )
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(note_events)")}
    if "order_no" not in cols:
        conn.execute("ALTER TABLE note_events ADD COLUMN order_no TEXT")
    # 컬럼 존재가 보장된 뒤 생성(신규 DB·구버전 DB 공통). 레거시 수동 이벤트는
    # order_no NULL 다수 허용 → 부분 유니크 인덱스.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_order_no "
        "ON note_events(order_no) WHERE order_no IS NOT NULL"
    )


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
    _migrate(conn)
    conn.commit()
    _conn = conn
    logger.info("notes SQLite ready: %s", NOTES_DB_PATH)
    return _conn


def get_conn() -> sqlite3.Connection:
    if _conn is None:
        return init()
    return _conn
