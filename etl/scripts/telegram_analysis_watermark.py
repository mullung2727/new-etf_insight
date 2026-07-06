"""증분 분석 워터마크 — `telegram_analysis_watermark` 테이블.

하루 3회(morning/close/evening) 증분 분석에서 각 채널이 마지막으로 처리한 post_id를
기록한다. 다음 run은 `post_id > last_post_id`인 새 글만 본다(post_id는 채널별 단조증가).
"""
from __future__ import annotations

import datetime as dt
import sqlite3

_CREATE_WATERMARK = """
CREATE TABLE IF NOT EXISTS telegram_analysis_watermark (
    channel TEXT PRIMARY KEY,
    last_post_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_WATERMARK)


def read_watermarks(con: sqlite3.Connection) -> dict[str, int]:
    """{channel: last_post_id}. 스키마는 호출자가 미리 ensure(읽기 전용 커넥션에서
    DDL 금지 — load_posts는 PRAGMA query_only=ON 커넥션으로 이 함수를 부른다)."""
    return {
        row[0]: row[1]
        for row in con.execute(
            "SELECT channel, last_post_id FROM telegram_analysis_watermark"
        )
    }


def advance_watermarks(con: sqlite3.Connection, channel_max: dict[str, int]) -> None:
    """채널별 max(post_id)로 전진. 단조증가라 뒤로 밀리지 않지만 안전하게 max()로 upsert."""
    ensure_schema(con)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for channel, post_id in channel_max.items():
        con.execute(
            """
            INSERT INTO telegram_analysis_watermark(channel, last_post_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(channel) DO UPDATE SET
                last_post_id=max(last_post_id, excluded.last_post_id),
                updated_at=excluded.updated_at
            """,
            (channel, post_id, now),
        )
