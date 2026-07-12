"""유튜브 종목 시그널 — `youtube_stock_insights` 테이블.

스펙: docs/youtube_tech.md §5.4
- UNIQUE(date_kst, ticker)
- 영상 단위 upsert 시 source_video_ids / mention_channels 병합
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3


_CREATE_STOCK_INSIGHTS = """
CREATE TABLE IF NOT EXISTS youtube_stock_insights (
    date_kst TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    mention_channels TEXT NOT NULL,
    source_video_ids TEXT NOT NULL,
    discovery_reason TEXT NOT NULL,
    analysis TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(date_kst, ticker)
)
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_STOCK_INSIGHTS)


def _merge_list(existing_json: str | None, new_items: list[str]) -> list[str]:
    try:
        cur = json.loads(existing_json or "[]")
    except json.JSONDecodeError:
        cur = []
    if not isinstance(cur, list):
        cur = []
    out: list[str] = []
    for x in [*cur, *new_items]:
        if x not in out:
            out.append(x)
    return out


def upsert_stock_from_video(
    con: sqlite3.Connection,
    *,
    date_kst: str,
    ticker: str,
    name: str,
    channel_id: str,
    video_id: str,
    discovery_reason: str,
    analysis: str | None = None,
) -> None:
    """한 영상에서 나온 종목 upsert. channels/video_ids 병합. analysis 있으면 갱신."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    row = con.execute(
        "SELECT mention_channels, source_video_ids, analysis FROM youtube_stock_insights "
        "WHERE date_kst=? AND ticker=?",
        (date_kst, ticker),
    ).fetchone()
    if row:
        channels = _merge_list(row[0], [channel_id])
        videos = _merge_list(row[1], [video_id])
        prev_analysis = row[2]
        new_analysis = analysis if analysis is not None else prev_analysis
        con.execute(
            """
            UPDATE youtube_stock_insights SET
                name=?,
                mention_channels=?,
                source_video_ids=?,
                discovery_reason=?,
                analysis=?,
                updated_at=?
            WHERE date_kst=? AND ticker=?
            """,
            (
                name,
                json.dumps(channels, ensure_ascii=False),
                json.dumps(videos, ensure_ascii=False),
                discovery_reason,
                new_analysis,
                now,
                date_kst,
                ticker,
            ),
        )
    else:
        con.execute(
            """
            INSERT INTO youtube_stock_insights(
                date_kst, ticker, name, mention_channels, source_video_ids,
                discovery_reason, analysis, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                date_kst,
                ticker,
                name,
                json.dumps([channel_id], ensure_ascii=False),
                json.dumps([video_id], ensure_ascii=False),
                discovery_reason,
                analysis,
                now,
                now,
            ),
        )
