"""Holdings router tests (PLAN_ETF_HOLDINGS_WATCH #1-2)."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import duck
import duck_watchlist
import duckdb
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


def test_latest_deltas_week_month(holdings_db: Path) -> None:
    con = sqlite3.connect(str(holdings_db))
    con.executemany(
        "INSERT INTO etf_holdings_daily VALUES (?,?,?,?,?,?,?,?)",
        [
            ("2026-10-02", "426030", "AAA", "Alpha", 10, 100, 9.0, "t"),  # 7일 이내라 d7 기준 아님
            ("2026-09-30", "426030", "AAA", "Alpha", 10, 100, 4.0, "t"),
            ("2026-09-30", "426030", "BBB", "Beta", 10, 100, 3.5, "t"),
            ("2026-09-01", "426030", "AAA", "Alpha", 10, 100, 2.0, "t"),
        ],
    )
    con.commit()
    con.close()
    r = _client().get("/holdings/latest", params={"etf": "426030"})
    rows = {row["code"]: row for row in r.json()}
    assert (rows["AAA"]["d7"], rows["BBB"]["d7"], rows["CCC"]["d7"]) == (1.0, -0.5, 2.9)
    assert (rows["AAA"]["d30"], rows["BBB"]["d30"], rows["CCC"]["d30"]) == (3.0, 3.0, 2.9)


@pytest.fixture()
def returns_holdings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """수익률 테스트용 보유 DB. 기준일 2026-10-08, 한국/미국/선물 각 1행."""
    db = tmp_path / "returns_holdings.sqlite3"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE etf_holdings_daily(
          asof TEXT, etf TEXT, code TEXT, name TEXT, qty REAL, amt_krw REAL,
          weight_pct REAL, fetched_at TEXT, PRIMARY KEY(asof, etf, code));
        """
    )
    con.executemany(
        "INSERT INTO etf_holdings_daily VALUES (?,?,?,?,?,?,?,?)",
        [
            ("2026-10-08", "385720", "005930", "Samsung", 10, 100, 26.0, "t"),
            ("2026-10-08", "426030", "NVDA US EQUITY", "NVIDIA", 10, 100, 8.0, "t"),
            ("2026-10-08", "426030", "NQZ6 INDEX", "Nasdaq Fut", 10, 100, 1.0, "t"),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(duck, "DUCKDB_PATH", db)
    return db


@pytest.fixture()
def price_dbs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    krx = tmp_path / "krx_ohlcv.duckdb"
    us = tmp_path / "us_ohlcv.duckdb"
    con = duckdb.connect(str(krx))
    con.execute(
        "CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR, market VARCHAR, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, "
        "trading_value DOUBLE, market_cap DOUBLE, list_shrs DOUBLE, cmp_prev DOUBLE)"
    )
    con.close()
    con = duckdb.connect(str(us))
    con.execute(
        "CREATE TABLE ohlcv(date VARCHAR, ticker VARCHAR, market VARCHAR, "
        "open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, "
        "trading_value DOUBLE, market_cap DOUBLE, list_shrs DOUBLE)"
    )
    con.close()
    monkeypatch.setattr(duck_watchlist, "KRX_DB_PATH", krx)
    monkeypatch.setattr(duck_watchlist, "US_DB_PATH", us)
    return krx, us


def _insert_krx(krx: Path, rows: list[tuple]) -> None:
    con = duckdb.connect(str(krx))
    con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.close()


def _insert_us(us: Path, rows: list[tuple]) -> None:
    con = duckdb.connect(str(us))
    con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.close()


def _krx_row(d: str, ticker: str, close: float, cmp_prev: float) -> tuple:
    return (d, ticker, "KOSPI", close, close, close, close, 1000, close * 1000, 1e12, 1e9, cmp_prev)


def _us_row(d: str, ticker: str, close: float) -> tuple:
    return (d, ticker, "NASDAQ", close, close, close, close, 1000, close * 1000, 1e12, 1e9)


def _holding(etf: str, code: str) -> dict:
    r = _client().get("/holdings/latest", params={"etf": etf})
    assert r.status_code == 200
    return {row["code"]: row for row in r.json()}[code]


def test_kr_simple_return(returns_holdings: Path, price_dbs: tuple[Path, Path]) -> None:
    krx, _ = price_dbs
    _insert_krx(krx, [
        _krx_row("20260908", "005930", 100, 3),
        _krx_row("20260930", "005930", 105, 5),
        _krx_row("20261008", "005930", 110, 5),
    ])
    assert _holding("385720", "005930")["r1m"] == 10.0


def test_kr_split_adjusted(returns_holdings: Path, price_dbs: tuple[Path, Path]) -> None:
    krx, _ = price_dbs
    _insert_krx(krx, [
        _krx_row("20260908", "005930", 50000, 100),
        _krx_row("20261008", "005930", 10200, 200),
    ])
    assert _holding("385720", "005930")["r1m"] == pytest.approx(2.0)


def test_us_simple_return(returns_holdings: Path, price_dbs: tuple[Path, Path]) -> None:
    _, us = price_dbs
    _insert_us(us, [
        _us_row("20260908", "NVDA", 100),
        _us_row("20261008", "NVDA", 90),
    ])
    assert _holding("426030", "NVDA US EQUITY")["r1m"] == -10.0


def test_no_earlier_price_r12m_none(returns_holdings: Path, price_dbs: tuple[Path, Path]) -> None:
    krx, _ = price_dbs
    _insert_krx(krx, [
        _krx_row("20260908", "005930", 100, 3),
        _krx_row("20261008", "005930", 110, 10),
    ])
    row = _holding("385720", "005930")
    assert row["r1m"] == 10.0
    assert row["r12m"] is None


def test_stale_end_price_returns_none(returns_holdings: Path, price_dbs: tuple[Path, Path]) -> None:
    krx, _ = price_dbs
    _insert_krx(krx, [
        _krx_row("20260808", "005930", 100, 3),
        _krx_row("20260918", "005930", 110, 10),
    ])
    row = _holding("385720", "005930")
    assert [row[k] for k in ("r1m", "r3m", "r6m", "r12m")] == [None] * 4


def test_futures_returns_none(returns_holdings: Path, price_dbs: tuple[Path, Path]) -> None:
    row = _holding("426030", "NQZ6 INDEX")
    assert [row[k] for k in ("r1m", "r3m", "r6m", "r12m")] == [None] * 4


def test_missing_price_dbs_ok(
    returns_holdings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(duck_watchlist, "KRX_DB_PATH", tmp_path / "no_krx.duckdb")
    monkeypatch.setattr(duck_watchlist, "US_DB_PATH", tmp_path / "no_us.duckdb")
    con = sqlite3.connect(str(returns_holdings))
    con.execute(
        "INSERT INTO etf_holdings_daily VALUES (?,?,?,?,?,?,?,?)",
        ("2026-10-07", "426030", "NVDA US EQUITY", "NVIDIA", 10, 100, 7.0, "t"),
    )
    con.commit()
    con.close()
    r = _client().get("/holdings/latest", params={"etf": "426030"})
    assert r.status_code == 200
    row = {x["code"]: x for x in r.json()}["NVDA US EQUITY"]
    assert row["d1"] == 1.0
    assert [row[k] for k in ("r1m", "r3m", "r6m", "r12m")] == [None] * 4
