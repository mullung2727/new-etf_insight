"""Holdings router tests (PLAN_ETF_HOLDINGS_WATCH #1-2)."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import duck
from main import app


@pytest.fixture()
def holdings_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db = tmp_path / "holdings.sqlite3"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE etf_holdings_daily(
          asof TEXT, etf TEXT, code TEXT, name TEXT, qty REAL, amt_krw REAL,
          weight_pct REAL, fetched_at TEXT, PRIMARY KEY(asof, etf, code));
        CREATE TABLE etf_holding_events(
          date TEXT, etf TEXT, code TEXT, name TEXT, kind TEXT, w_prev REAL, w_cur REAL,
          PRIMARY KEY(date, etf, code, kind));
        """
    )
    con.executemany(
        "INSERT INTO etf_holdings_daily VALUES (?,?,?,?,?,?,?,?)",
        [
            ("2026-10-07", "426030", "AAA", "Alpha", 10, 100, 6.0, "t"),
            ("2026-10-07", "426030", "BBB", "Beta", 10, 100, 4.0, "t"),
            ("2026-10-08", "426030", "AAA", "Alpha", 10, 100, 5.0, "t"),
            ("2026-10-08", "426030", "BBB", "Beta", 10, 100, 3.0, "t"),
            ("2026-10-08", "426030", "CCC", "Gamma", 10, 100, 2.9, "t"),
            ("2026-10-08", "385720", "005930", "Samsung", 10, 100, 26.0, "t"),
        ],
    )
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    con.executemany(
        "INSERT INTO etf_holding_events VALUES (?,?,?,?,?,?,?)",
        [
            (today, "426030", "AAA", "Alpha", "DN3", 6.0, 5.0),
            (today, "426030", "CCC", "Gamma", "ENTRY", 0.0, 2.9),
            (week_ago, "385720", "005930", "Samsung", "UP5", 4.9, 26.0),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(duck, "DUCKDB_PATH", db)
    return db


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_latest_returns_newest_with_flags(holdings_db: Path) -> None:
    r = _client().get("/holdings/latest", params={"etf": "426030"})
    assert r.status_code == 200
    rows = {row["code"]: row for row in r.json()}
    assert set(rows) == {"AAA", "BBB", "CCC"}
    assert rows["AAA"]["asof"] == "2026-10-08"
    assert rows["AAA"]["is_conviction"] is True and rows["AAA"]["is_faded"] is False
    assert rows["BBB"]["is_conviction"] is False and rows["BBB"]["is_faded"] is True
    assert rows["CCC"]["is_conviction"] is False and rows["CCC"]["is_faded"] is True


def test_latest_unknown_etf_empty(holdings_db: Path) -> None:
    r = _client().get("/holdings/latest", params={"etf": "000000"})
    assert r.status_code == 200
    assert r.json() == []


def test_events_filter_and_order(holdings_db: Path) -> None:
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    r = _client().get("/holdings/events", params={"days": 30})
    assert r.status_code == 200
    rows = r.json()
    assert [x["date"] for x in rows] == [today, today, week_ago]
    r = _client().get("/holdings/events", params={"etf": "385720", "days": 30})
    assert [x["code"] for x in r.json()] == ["005930"]
    r = _client().get("/holdings/events", params={"days": 1})
    assert {x["code"] for x in r.json()} == {"AAA", "CCC"}


def test_history_ascending(holdings_db: Path) -> None:
    r = _client().get("/holdings/history", params={"etf": "426030", "code": "AAA"})
    assert r.status_code == 200
    assert [(x["asof"], x["weight_pct"]) for x in r.json()] == [
        ("2026-10-07", 6.0),
        ("2026-10-08", 5.0),
    ]


def test_latest_deltas(holdings_db: Path) -> None:
    r = _client().get("/holdings/latest", params={"etf": "426030"})
    assert r.status_code == 200
    rows = {row["code"]: row for row in r.json()}
    assert rows["AAA"]["d1"] == -1.0
    assert rows["BBB"]["d1"] == -1.0
    assert rows["CCC"]["d1"] == 2.9
    for row in rows.values():
        assert row["d7"] is None
        assert row["d30"] is None
