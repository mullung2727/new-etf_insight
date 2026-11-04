"""build_krx_from_minute 단위 테스트 (임시 DuckDB 파일, 실 DB 금지)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd

from scripts.build_krx_from_minute import (
    aggregate_day,
    build_rows,
    prev_trading_day,
    run,
)
from scripts.build_krx_ohlcv import _CREATE_OHLCV, ensure_provisional_table


_CREATE_MINUTE_BARS = """
CREATE TABLE minute_bars (
    ticker VARCHAR, scope VARCHAR, timestamp VARCHAR,
    date VARCHAR, time VARCHAR,
    open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT
)
"""

_CREATE_MINUTE_FETCHED = """
CREATE TABLE minute_fetched (
    ticker VARCHAR, scope VARCHAR, date VARCHAR,
    status VARCHAR DEFAULT 'complete',
    PRIMARY KEY (ticker, scope, date)
)
"""


def _bar(ticker, date, time, o, h, l, c, v, scope="1"):
    return (ticker, scope, date + time, date, time, o, h, l, c, v)


def _ohlcv_row(date, ticker, market="KOSPI", close=100, shrs=1000):
    return (date, ticker, market, close - 10, close + 10, close - 20, close,
            500, close * 500, close * shrs, shrs, 5)


class _TempDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mcon = duckdb.connect(str(Path(self.tmp.name) / "minute_bars.duckdb"))
        self.kcon = duckdb.connect(str(Path(self.tmp.name) / "krx_ohlcv.duckdb"))
        self.mcon.execute(_CREATE_MINUTE_BARS)
        self.mcon.execute(_CREATE_MINUTE_FETCHED)
        self.kcon.execute(_CREATE_OHLCV)
        ensure_provisional_table(self.kcon)

    def tearDown(self):
        self.mcon.close()
        self.kcon.close()
        self.tmp.cleanup()

    def _insert_bars(self, bars):
        self.mcon.executemany(
            "INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?,?)", bars
        )

    def _mark_fetched(self, ticker, date, scope="1", status="complete"):
        self.mcon.execute(
            "INSERT INTO minute_fetched VALUES (?,?,?,?)",
            [ticker, scope, date, status],
        )

    def _insert_ohlcv(self, row):
        self.kcon.execute("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)


class TestAggregateDay(_TempDbTest):
    def test_basic_ohlc_ignores_after_hours(self):
        self._insert_bars([
            _bar("A", "20250106", "090000", 100, 110, 90, 105, 1000),
            _bar("A", "20250106", "120000", 105, 120, 100, 115, 2000),
            _bar("A", "20250106", "153000", 115, 118, 112, 117, 3000),
            _bar("A", "20250106", "160000", 117, 130, 117, 130, 9999),
        ])
        self._mark_fetched("A", "20250106")
        df = aggregate_day(self.mcon, "20250106")
        self.assertEqual(len(df), 1)
        row = df.iloc[0]
        self.assertEqual(row["ticker"], "A")
        self.assertEqual(row["open"], 100)
        self.assertEqual(row["high"], 120)
        self.assertEqual(row["low"], 90)
        self.assertEqual(row["close"], 117)
        self.assertEqual(row["volume"], 6000)
        self.assertEqual(row["trading_value"], 105 * 1000 + 115 * 2000 + 117 * 3000)
        self.assertTrue(bool(row["has1530"]))

    def test_close_is_last_bar_without_1530(self):
        self._insert_bars([
            _bar("A", "20250106", "090000", 100, 110, 90, 105, 1000),
            _bar("A", "20250106", "152000", 105, 112, 103, 110, 500),
        ])
        self._mark_fetched("A", "20250106")
        df = aggregate_day(self.mcon, "20250106")
        row = df.iloc[0]
        self.assertEqual(row["close"], 110)
        self.assertFalse(bool(row["has1530"]))

    def test_empty_day_returns_empty(self):
        df = aggregate_day(self.mcon, "20250106")
        self.assertEqual(len(df), 0)

    def test_unmarked_ticker_excluded(self):
        self._insert_bars([
            _bar("A", "20250106", "090000", 100, 110, 90, 105, 1000),
            _bar("B", "20250106", "090000", 200, 210, 190, 205, 500),
        ])
        self._mark_fetched("A", "20250106")
        df = aggregate_day(self.mcon, "20250106")
        self.assertEqual(sorted(df["ticker"].tolist()), ["A"])

    def test_empty_status_with_bars_excluded(self):
        self._insert_bars([
            _bar("A", "20250106", "090000", 100, 110, 90, 105, 1000),
            _bar("B", "20250106", "090000", 200, 210, 190, 205, 500),
        ])
        self._mark_fetched("A", "20250106")
        self._mark_fetched("B", "20250106", status="empty")
        df = aggregate_day(self.mcon, "20250106")
        self.assertEqual(sorted(df["ticker"].tolist()), ["A"])


class TestBuildRows(_TempDbTest):
    def _agg(self, ticker="A", close=500, **kw):
        base = {"ticker": ticker, "open": 460, "high": 510, "low": 455,
                "close": close, "volume": 100, "trading_value": 50000,
                "has1530": True}
        base.update(kw)
        return pd.DataFrame([base])

    def test_basic_cap_and_cmp(self):
        self._insert_ohlcv(_ohlcv_row("20250102", "A", close=450, shrs=1000))
        rows, skipped = build_rows(self.kcon, self._agg(), "20250103")
        self.assertEqual(skipped, 0)
        self.assertEqual(
            rows,
            [("20250103", "A", "KOSPI", 460, 510, 455, 500,
              100, 50000, 500000, 1000, 50)],
        )

    def test_corp_move_excluded(self):
        self._insert_ohlcv(_ohlcv_row("20250102", "A", close=10000, shrs=100))
        rows, skipped = build_rows(self.kcon, self._agg(close=2000), "20250103")
        self.assertEqual(rows, [])
        self.assertEqual(skipped, 1)

    def test_prev_skips_provisional_date(self):
        self._insert_ohlcv(_ohlcv_row("20250102", "A", market="KOSPI", close=450, shrs=1000))
        self._insert_ohlcv(_ohlcv_row("20250103", "A", market="KOSDAQ", close=999, shrs=5))
        self.kcon.execute("INSERT INTO ohlcv_provisional VALUES ('20250103', 1, 0, 'ts')")
        rows, skipped = build_rows(self.kcon, self._agg(), "20250106")
        self.assertEqual(skipped, 0)
        self.assertEqual(rows[0][0], "20250106")
        self.assertEqual(rows[0][2], "KOSPI")    # 01-02 값 (01-03 건너뜀)
        self.assertEqual(rows[0][9], 500000)     # 500 x 1000
        self.assertEqual(rows[0][11], 50)        # 500 - 450


class TestRun(_TempDbTest):
    def test_exists_writes_nothing(self):
        self._insert_ohlcv(_ohlcv_row("20250106", "A"))
        res = run("20250107", self.kcon, self.mcon)
        self.assertEqual(res, {"status": "exists", "date": "20250106"})
        self.assertEqual(
            self.kcon.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0], 1)
        self.assertEqual(
            self.kcon.execute("SELECT COUNT(*) FROM ohlcv_provisional").fetchone()[0], 0)

    def test_no_minute(self):
        self._insert_ohlcv(_ohlcv_row("20250103", "A"))
        res = run("20250107", self.kcon, self.mcon)
        self.assertEqual(res, {"status": "no_minute", "date": "20250106"})

    def test_no_minute_when_all_rows_excluded(self):
        # 분봉은 있지만 전 종목 제외 (A: ±30% 밖, B: 전날 없음) → 아무것도 안 씀
        self._insert_ohlcv(_ohlcv_row("20250103", "A", close=10000, shrs=100))
        self._insert_bars([
            _bar("A", "20250106", "090000", 1900, 2100, 1800, 2000, 100),
            _bar("A", "20250106", "153000", 2000, 2050, 1950, 2000, 200),
            _bar("B", "20250106", "090000", 90, 110, 85, 100, 50),
        ])
        self._mark_fetched("A", "20250106")
        self._mark_fetched("B", "20250106")
        res = run("20250107", self.kcon, self.mcon)
        self.assertEqual(res, {"status": "no_minute", "date": "20250106"})
        self.assertEqual(
            self.kcon.execute(
                "SELECT COUNT(*) FROM ohlcv WHERE date = '20250106'"
            ).fetchone()[0], 0)
        self.assertEqual(
            self.kcon.execute("SELECT COUNT(*) FROM ohlcv_provisional").fetchone()[0], 0)
        self.assertEqual(
            self.kcon.execute(
                "SELECT COUNT(*) FROM ohlcv_provisional_no1530"
            ).fetchone()[0], 0)

    def test_loaded_writes_provisional_and_no1530(self):
        self._insert_ohlcv(_ohlcv_row("20250103", "A", market="KOSPI", close=450, shrs=1000))
        self._insert_ohlcv(_ohlcv_row("20250103", "B", market="KOSDAQ", close=100, shrs=2000))
        self._insert_bars([
            _bar("A", "20250106", "090000", 460, 470, 455, 465, 100),
            _bar("A", "20250106", "153000", 465, 510, 460, 500, 200),
            _bar("B", "20250106", "090000", 105, 108, 102, 107, 50),
            _bar("B", "20250106", "152000", 107, 112, 106, 110, 60),
        ])
        self._mark_fetched("A", "20250106")
        self._mark_fetched("B", "20250106")
        res = run("20250107", self.kcon, self.mcon)
        self.assertEqual(res["status"], "loaded")
        self.assertEqual(res["date"], "20250106")
        self.assertEqual(res["rows"], 2)
        self.assertEqual(res["skipped_corp"], 0)
        prov = self.kcon.execute(
            "SELECT date, rows, skipped_corp FROM ohlcv_provisional"
        ).fetchall()
        self.assertEqual(prov, [("20250106", 2, 0)])
        no1530 = self.kcon.execute(
            "SELECT date, ticker FROM ohlcv_provisional_no1530 ORDER BY ticker"
        ).fetchall()
        self.assertEqual(no1530, [("20250106", "B")])
        got = self.kcon.execute(
            "SELECT ticker, close, market_cap, cmp_prev FROM ohlcv "
            "WHERE date = '20250106' ORDER BY ticker"
        ).fetchall()
        self.assertEqual(got, [("A", 500, 500000, 50), ("B", 110, 220000, 10)])


class TestPrevTradingDay(unittest.TestCase):
    def test_monday_goes_to_friday(self):
        self.assertEqual(prev_trading_day("20250106"), "20250103")

    def test_skips_holiday(self):
        with patch(
            "scripts.build_krx_from_minute.is_krx_trading_day",
            side_effect=[False, False, False, True],
        ):
            self.assertEqual(prev_trading_day("20250106"), "20250102")


if __name__ == "__main__":
    unittest.main()
