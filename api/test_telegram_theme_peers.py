"""/telegram/theme-peers/{ticker} 검증.

핵심:
  - 대상 종목의 themes(전 기간 합집합)와 theme 겹치는 다른 종목만
  - 공유 theme 수 내림차순, 자기 자신 제외
  - themes 없는/데이터 없는 대상 → []

Run from api/:
    uv run --with pytest --with httpx pytest test_telegram_theme_peers.py
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


def _row(con, ticker, name, themes):
    con.execute(
        "INSERT INTO telegram_stock_insights "
        "(date_kst, session, ticker, name, mention_channels, source_post_refs, "
        "discovery_reason, analysis) VALUES (?,?,?,?,?,?,?,?)",
        ("2026-07-06", "evening", ticker, name, "[]", "[]", "x",
         json.dumps({"themes": themes}) if themes is not None else None),
    )


@pytest.fixture
def temp_db(monkeypatch):
    d = Path(tempfile.mkdtemp())
    db = d / "telegram_public.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute(_DDL)
    _row(con, "005930", "삼성전자", ["반도체", "HBM"])       # 대상
    _row(con, "900000", "둘공유", ["반도체", "HBM"])         # 2개 공유 → 1위
    _row(con, "000660", "하이닉스", ["HBM", "메모리"])       # 1개 공유
    _row(con, "373220", "엘지엔솔", ["반도체", "2차전지"])   # 1개 공유
    _row(con, "111111", "무관", ["바이오"])                  # 공유 없음
    _row(con, "222222", "미분석", None)                       # analysis 없음
    con.commit()
    con.close()
    monkeypatch.setattr(duck_watchlist, "TELEGRAM_DB_PATH", db)
    yield
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_peers_share_theme_exclude_self(temp_db):
    body = client.get("/telegram/theme-peers/005930").json()
    tickers = [p["ticker"] for p in body]
    assert "005930" not in tickers          # 자기 제외
    assert "111111" not in tickers          # 공유 없음 제외
    assert set(tickers) == {"900000", "000660", "373220"}


def test_sorted_by_shared_count_desc(temp_db):
    body = client.get("/telegram/theme-peers/005930").json()
    assert body[0]["ticker"] == "900000"    # 2개 공유 최상단
    assert sorted(body[0]["themes"]) == ["HBM", "반도체"]


def test_shared_themes_only(temp_db):
    body = client.get("/telegram/theme-peers/005930").json()
    hynix = next(p for p in body if p["ticker"] == "000660")
    assert hynix["themes"] == ["HBM"]       # 메모리는 공유 아님 → 제외


def test_theme_with_no_other_holder_returns_empty(temp_db):
    # 111111(바이오)는 자기 theme는 있으나 그 theme를 가진 다른 종목이 없음 → 빈
    assert client.get("/telegram/theme-peers/111111").json() == []


def test_unknown_ticker_returns_empty(temp_db):
    assert client.get("/telegram/theme-peers/999999").json() == []
