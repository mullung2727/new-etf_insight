"""세션별 Telegram 중요 내용 하이라이트 SQLite 저장소."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


_SCHEMA = """
CREATE TABLE IF NOT EXISTS telegram_session_highlights (
    date_kst TEXT NOT NULL,
    session TEXT NOT NULL,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    category TEXT NOT NULL,
    importance_reason TEXT NOT NULL,
    score_total INTEGER NOT NULL CHECK(score_total BETWEEN 0 AND 100),
    score_breakdown_json TEXT NOT NULL,
    source_channels_json TEXT NOT NULL,
    source_post_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (date_kst, session, rank)
)
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_SCHEMA)


def replace_session_highlights(
    con: sqlite3.Connection,
    date_kst: str,
    session: str,
    rows: list[dict],
) -> None:
    """한 세션의 하이라이트를 순위순으로 완전 교체한다."""
    ensure_schema(con)
    con.execute(
        "DELETE FROM telegram_session_highlights WHERE date_kst=? AND session=?",
        (date_kst, session),
    )
    now = datetime.now(timezone.utc).isoformat()
    for rank, row in enumerate(rows, 1):
        con.execute(
            """
            INSERT INTO telegram_session_highlights (
                date_kst, session, rank, title, summary, category,
                importance_reason, score_total, score_breakdown_json,
                source_channels_json, source_post_refs_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                date_kst,
                session,
                rank,
                row["title"],
                row["summary"],
                row["category"],
                row["importance_reason"],
                int(row["score_total"]),
                json.dumps(row.get("score_breakdown", {}), ensure_ascii=False),
                json.dumps(row.get("source_channels", []), ensure_ascii=False),
                json.dumps(row.get("source_post_refs", []), ensure_ascii=False),
                now,
                now,
            ),
        )


def fetch_session_highlights(
    con: sqlite3.Connection,
    date_kst: str,
    session: str,
) -> list[dict]:
    cur = con.execute(
        """
        SELECT rank, title, summary, category, importance_reason, score_total,
               score_breakdown_json, source_channels_json, source_post_refs_json
        FROM telegram_session_highlights
        WHERE date_kst=? AND session=?
        ORDER BY rank
        """,
        (date_kst, session),
    )
    out = []
    for row in cur.fetchall():
        out.append(
            {
                "rank": row[0],
                "title": row[1],
                "summary": row[2],
                "category": row[3],
                "importance_reason": row[4],
                "score_total": row[5],
                "score_breakdown": json.loads(row[6]),
                "source_channels": json.loads(row[7]),
                "source_post_refs": json.loads(row[8]),
            }
        )
    return out
