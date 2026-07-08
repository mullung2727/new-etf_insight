"""/telegram/digest/latest 검증.

핵심:
  - 여러 (date, session) 중 가장 최근 하나만 반환 (session 시간순: morning<close<evening)
  - analysis IS NULL 행은 제외
  - 신규(new) 먼저, 그다음 채널 수 내림차순
  - 데이터 없으면 null

Run from api/:
    uv run --with pytest --with httpx pytest test_telegram_digest.py
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


def _row(con, date, session, ticker, name, channels, analysis):
    con.execute(
        "INSERT INTO telegram_stock_insights "
        "(date_kst, session, ticker, name, mention_channels, source_post_refs, "
        "discovery_reason, analysis) VALUES (?,?,?,?,?,?,?,?)",
        (date, session, ticker, name, json.dumps(channels), "[]", "x",
         json.dumps(analysis) if analysis is not None else None),
    )


@pytest.fixture
def temp_db(monkeypatch):
    d = Path(tempfile.mkdtemp())
    db = d / "telegram_public.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute(_DDL)
    # 과거 세션 (무시돼야)
    _row(con, "2026-07-05", "close", "111111", "구", ["a"],
         {"change_type": "new", "change_summary": "old", "themes": []})
    # 최신 날짜의 이른 세션 (close) — evening 있으면 무시돼야
    _row(con, "2026-07-06", "close", "222222", "낮", ["a"],
         {"change_type": "new", "change_summary": "noon", "themes": ["x"]})
    # 최신 날짜의 최신 세션 (evening) — 이게 반환돼야
    _row(con, "2026-07-06", "evening", "005930", "삼성", ["a", "b", "c"],
         {"change_type": "continued", "change_summary": "지속", "themes": ["반도체"]})
    _row(con, "2026-07-06", "evening", "000660", "하이닉스", ["a"],
         {"change_type": "new", "change_summary": "신규진입", "themes": ["HBM", "반도체"]})
    # 분석 안 된 후보 (제외돼야)
    _row(con, "2026-07-06", "evening", "999999", "미분석", ["a", "b"], None)
    con.commit()
    con.close()
    monkeypatch.setattr(duck_watchlist, "TELEGRAM_DB_PATH", db)
    yield
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_latest_session_selected_and_sorted(temp_db):
    body = client.get("/telegram/digest/latest").json()
    assert body["date"] == "2026-07-06"
    assert body["session"] == "evening"       # close 아님 (시간순 최신)
    assert body["count"] == 2                  # 미분석(999999) 제외
    # 신규(하이닉스) 먼저, 그다음 지속(삼성)
    assert [i["ticker"] for i in body["items"]] == ["000660", "005930"]
    assert body["items"][0]["channels"] == 1
    assert body["items"][1]["channels"] == 3   # 삼성 3채널
    assert body["items"][1]["themes"] == ["반도체"]


def test_null_when_no_analysis(temp_db, monkeypatch):
    d = Path(tempfile.mkdtemp())
    db = d / "telegram_public.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute(_DDL)
    _row(con, "2026-07-06", "close", "111111", "미분석", ["a"], None)
    con.commit()
    con.close()
    monkeypatch.setattr(duck_watchlist, "TELEGRAM_DB_PATH", db)
    assert client.get("/telegram/digest/latest").json() is None
    import shutil
    shutil.rmtree(d, ignore_errors=True)
