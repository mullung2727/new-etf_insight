"""크로스채널 종목탐색/분석 결과 — `telegram_stock_insights` 테이블.

종목(ticker) 기준이며 `feed_role=discovery_source` 채널들의 원문을 재조합한 결과를
담는다. 탐색(upsert_candidate)과 분석(update_analysis)은 별개 단계 — 탐색 재실행이
이미 채워진 analysis를 지우면 안 된다.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

_CREATE_STOCK_INSIGHTS = """
CREATE TABLE IF NOT EXISTS telegram_stock_insights (
    date_kst TEXT NOT NULL,
    session TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT NOT NULL,
    mention_channels TEXT NOT NULL,
    source_post_refs TEXT NOT NULL,
    discovery_reason TEXT NOT NULL,
    analysis TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(date_kst, session, ticker)
)
"""


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute(_CREATE_STOCK_INSIGHTS)


def upsert_candidate(
    con: sqlite3.Connection,
    date_kst: str,
    session: str,
    ticker: str,
    name: str,
    *,
    mention_channels: list[str],
    source_post_refs: list[str],
    discovery_reason: str,
) -> None:
    """탐색 단계 upsert. `analysis`는 절대 건드리지 않는다.

    키는 `(date_kst, session, ticker)` — 하루 3회 증분 분석이라 같은 종목이
    세션별로 별도 행(신규→지속 이력이 세션 단위로 쌓임)."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute(
        """
        INSERT INTO telegram_stock_insights(
            date_kst, session, ticker, name, mention_channels, source_post_refs,
            discovery_reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date_kst, session, ticker) DO UPDATE SET
            name=excluded.name,
            mention_channels=excluded.mention_channels,
            source_post_refs=excluded.source_post_refs,
            discovery_reason=excluded.discovery_reason,
            updated_at=excluded.updated_at
        """,
        (
            date_kst, session, ticker, name,
            json.dumps(mention_channels, ensure_ascii=False),
            json.dumps(source_post_refs, ensure_ascii=False),
            discovery_reason, now, now,
        ),
    )


def update_analysis(
    con: sqlite3.Connection, date_kst: str, session: str, ticker: str, analysis: str
) -> None:
    """분석 단계 upsert. 탐색 필드는 건드리지 않는다."""
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute(
        "UPDATE telegram_stock_insights SET analysis=?, updated_at=? "
        "WHERE date_kst=? AND session=? AND ticker=?",
        (analysis, now, date_kst, session, ticker),
    )
