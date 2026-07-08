"""/telegram/mentions/{ticker} 검증.

핵심:
  - 해당 ticker 언급 이력만, 최신순(date desc, session 시간순 desc)
  - from/to(date_kst) + session 필터
  - mention_channels/source_post_refs/analysis JSON 파싱 → 배열/필드
  - 미분석(analysis NULL) 행도 언급이력으로 포함(change_* 는 null)

Run from api/:
    uv run --with pytest --with httpx pytest test_telegram_mentions.py
"""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

import duck_watchlist
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

_DDL = """
CREATE TABLE telegram_stock_insights (
    date_kst TEXT NOT NULL, session TEXT NOT NULL, ticker TEXT NOT NULL,
    name TEXT NOT NULL, mention_channels TEXT NOT NULL, source_post_refs TEXT NOT NULL,
    discovery_reason TEXT NOT NULL, analysis TEXT,
    UNIQUE(date_kst, session, ticker)
)
"""


def _row(con, date, session, ticker, channels, refs, analysis):
    con.execute(
        "INSERT INTO telegram_stock_insights "
        "(date_kst, session, ticker, name, mention_channels, source_post_refs, "
        "discovery_reason, analysis) VALUES (?,?,?,?,?,?,?,?)",
        (date, session, ticker, "삼성", json.dumps(channels), json.dumps(refs), "x",
         json.dumps(analysis) if analysis is not None else None),
    )


@pytest.fixture
def temp_db(monkeypatch):
    d = Path(tempfile.mkdtemp())
    db = d / "telegram_public.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute(_DDL)
    # 005930 언급 3건 (서로 다른 날짜/세션) + 다른 종목 1건(제외돼야)
    _row(con, "2026-07-05", "close", "005930", ["ch_a"], ["ch_a/1"],
         {"change_type": "new", "change_summary": "첫언급", "themes": ["반도체"]})
    _row(con, "2026-07-06", "close", "005930", ["ch_a", "ch_b"], ["ch_a/2", "ch_b/9"],
         {"change_type": "continued", "change_summary": "낮", "themes": ["HBM"]})
    _row(con, "2026-07-06", "evening", "005930", ["ch_c"], ["ch_c/3"], None)  # 미분석
    _row(con, "2026-07-06", "evening", "000660", ["ch_x"], ["ch_x/1"],
         {"change_type": "new", "change_summary": "타종목", "themes": []})
    con.commit()
    con.close()
    monkeypatch.setattr(duck_watchlist, "TELEGRAM_DB_PATH", db)
    yield
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_returns_ticker_history_newest_first(temp_db):
    body = client.get("/telegram/mentions/005930").json()
    assert len(body) == 3  # 000660 제외
    # 최신순: (07-06 evening) > (07-06 close) > (07-05 close)
    assert [(m["date_kst"], m["session"]) for m in body] == [
        ("2026-07-06", "evening"),
        ("2026-07-06", "close"),
        ("2026-07-05", "close"),
    ]


def test_parses_channels_refs_and_analysis(temp_db):
    body = client.get("/telegram/mentions/005930").json()
    noon = next(m for m in body if m["session"] == "close" and m["date_kst"] == "2026-07-06")
    assert noon["channels"] == ["ch_a", "ch_b"]
    assert noon["post_refs"] == ["ch_a/2", "ch_b/9"]
    assert noon["change_type"] == "continued"
    assert noon["themes"] == ["HBM"]


def test_unanalyzed_row_included_with_null_change(temp_db):
    body = client.get("/telegram/mentions/005930").json()
    ev = next(m for m in body if m["session"] == "evening")
    assert ev["change_type"] is None
    assert ev["channels"] == ["ch_c"]        # 언급정보는 유지
    assert ev["post_refs"] == ["ch_c/3"]


def test_date_range_filter(temp_db):
    body = client.get("/telegram/mentions/005930?from=2026-07-06&to=2026-07-06").json()
    assert len(body) == 2
    assert all(m["date_kst"] == "2026-07-06" for m in body)


def test_session_filter(temp_db):
    body = client.get("/telegram/mentions/005930?session=close").json()
    assert len(body) == 2
    assert all(m["session"] == "close" for m in body)
