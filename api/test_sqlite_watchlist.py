"""STEP4 값-정확성 테스트 — watchlist reader 의 SQLite 전환 검증.

watchlist/llm_scores 는 SQLite, ohlcv(krx)는 DuckDB 유지. 검증:
  - /watchlist 그룹핑 {date: [code]}
  - /watchlist/scores/{date} score DESC NULLS LAST 정렬 + sources JSON 디코드
  - /ohlcv/{ticker} 는 krx(DuckDB)에서 캔들 오름차순 + limit

Run from api/:
    uv run --with pytest --with httpx pytest test_sqlite_watchlist.py
"""
import sqlite3
import tempfile
from pathlib import Path

import duckdb
import pytest

import duck_watchlist
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

_WL_DDL = "CREATE TABLE watchlist (date TEXT, stock_code TEXT, PRIMARY KEY(date, stock_code))"
_LS_DDL = """
CREATE TABLE llm_scores (
    date TEXT, ticker TEXT, name TEXT, ratio REAL, today_volume INTEGER,
    avg5_volume INTEGER, trading_value INTEGER, close INTEGER, score INTEGER,
    category TEXT, reason_summary TEXT, final_opinion TEXT, evidence_board TEXT,
    evidence_news TEXT, evidence_web TEXT, sources TEXT, PRIMARY KEY(date, ticker)
)
"""


@pytest.fixture(autouse=True)
def temp_dbs(monkeypatch):
    d = Path(tempfile.mkdtemp())
    wl = d / "watchlist.sqlite3"
    krx = d / "krx_ohlcv.duckdb"

    con = sqlite3.connect(str(wl))
    con.execute(_WL_DDL)
    con.execute(_LS_DDL)
    con.executemany("INSERT INTO watchlist VALUES (?,?)", [
        ("20260615", "069500"), ("20260615", "005930"), ("20260616", "000660"),
    ])
    # score: A=90, B=None(→NULLS LAST), C=70. sources 는 JSON 문자열.
    base = [None] * 13
    con.execute(
        "INSERT INTO llm_scores (date,ticker,name,score,sources) VALUES (?,?,?,?,?)",
        ("20260615", "AAA", "에이", 90, '["http://a"]'),
    )
    con.execute(
        "INSERT INTO llm_scores (date,ticker,name,score,sources) VALUES (?,?,?,?,?)",
        ("20260615", "BBB", "비", None, '["http://b"]'),
    )
    con.execute(
        "INSERT INTO llm_scores (date,ticker,name,score,sources) VALUES (?,?,?,?,?)",
        ("20260615", "CCC", "씨", 70, "not-json"),
    )
    con.commit()
    con.close()

    kcon = duckdb.connect(str(krx))
    kcon.execute(
        "CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, open INTEGER, high INTEGER, "
        "low INTEGER, close INTEGER, volume BIGINT, trading_value BIGINT)"
    )
    kcon.executemany(
        "INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?)",
        [
            ("20260613", "069500", 100, 110, 95, 105, 1000, 105000),
            ("20260614", "069500", 105, 120, 100, 115, 2000, 230000),
            ("20260615", "069500", 115, 130, 110, 125, 3000, 375000),
        ],
    )
    kcon.close()

    monkeypatch.setattr(duck_watchlist, "WATCHLIST_DB_PATH", wl)
    monkeypatch.setattr(duck_watchlist, "KRX_DB_PATH", krx)
    yield
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_watchlist_grouped_by_date():
    body = client.get("/watchlist").json()
    assert body == {"20260615": ["005930", "069500"], "20260616": ["000660"]}


def test_scores_order_nulls_last_and_sources_decoded():
    rows = client.get("/watchlist/scores/20260615").json()
    assert [r["ticker"] for r in rows] == ["AAA", "CCC", "BBB"]  # 90,70,NULL
    by = {r["ticker"]: r for r in rows}
    assert by["AAA"]["sources"] == ["http://a"]      # JSON 디코드
    assert by["CCC"]["sources"] == "not-json"        # 비JSON은 원문 통과


def test_ohlcv_ascending_and_limit():
    candles = client.get("/ohlcv/069500", params={"limit": 2}).json()
    # 최근 2개를 오름차순으로
    assert [c["date"] for c in candles] == ["20260614", "20260615"]
    assert candles[-1]["close"] == 125


def test_ohlcv_range_filter():
    candles = client.get("/ohlcv/069500", params={"begin": "20260613", "end": "20260614"}).json()
    assert [c["date"] for c in candles] == ["20260613", "20260614"]
