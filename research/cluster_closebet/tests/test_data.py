"""DB 배선 TDD — 0값 제외·주간행렬·유니버스 필터.

실행: repo root에서
`etl\\.venv\\Scripts\\python.exe -m unittest research.cluster_closebet.tests.test_data -v`
"""
import unittest

import duckdb

from research.cluster_closebet.data import (
    build_weekly_matrix,
    load_daily,
)


def _db(rows):
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, market VARCHAR,"
        " open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,"
        " market_cap BIGINT, list_shrs BIGINT, trading_value BIGINT)"
    )
    con.executemany(
        "INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute(
        "CREATE TABLE stock_names (code VARCHAR PRIMARY KEY,"
        " name VARCHAR, updated_at VARCHAR)"
    )
    return con


def _row(day, close, ticker="000010", vol=1000, shrs=1_000_000):
    return (f"202401{day:02d}", ticker, "KOSPI", close, close, close,
            close, vol, close * shrs, shrs, close * vol)


class TestLoadDaily(unittest.TestCase):
    def test_zero_volume_excluded(self):
        con = _db([_row(1, 10000), _row(2, 10100, vol=0)])
        rows = load_daily(con, "20240101", "20240131")
        self.assertEqual([r["date"] for r in rows], ["20240101"])

    def test_turnover_fallback(self):
        con = duckdb.connect(":memory:")
        con.execute(
            "CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, market VARCHAR,"
            " open BIGINT, high BIGINT, low BIGINT, close BIGINT, volume BIGINT,"
            " market_cap BIGINT, list_shrs BIGINT, trading_value BIGINT)")
        con.execute(
            "CREATE TABLE stock_names (code VARCHAR PRIMARY KEY,"
            " name VARCHAR, updated_at VARCHAR)")
        con.execute(
            "INSERT INTO ohlcv VALUES ('20240101','000010','KOSPI',100,100,100,"
            "100,10,1000,10,NULL)")
        rows = load_daily(con, "20240101", "20240131")
        self.assertEqual(rows[0]["turnover"], 1000)


class TestUniverseFilter(unittest.TestCase):
    def test_preferred_and_spac_excluded(self):
        con = _db([_row(1, 10000, ticker="000010"),
                   _row(1, 5000, ticker="002787"),
                   _row(1, 2000, ticker="123450")])
        con.executemany(
            "INSERT INTO stock_names VALUES (?,?,?)",
            [("000010", "삼성전자", "2024-01-01"),
             ("002787", "진흥기업2우B", "2024-01-01"),
             ("123450", "○○스팩7호", "2024-01-01")])
        rows = load_daily(con, "20240101", "20240131")
        self.assertEqual({r["ticker"] for r in rows}, {"000010"})


class TestWeeklyMatrix(unittest.TestCase):
    def _bars(self, days, close_fn, ticker="000001"):
        return [{"ticker": ticker, "date": f"202401{d:02d}", "close": close_fn(d),
                 "list_shrs": 1_000_000} for d in days]

    def test_values_and_keys(self):
        rows = self._bars(range(1, 6), lambda d: 10000 * 1.01 ** (d - 1))
        grid, mat = build_weekly_matrix(rows, weeks=1)
        self.assertEqual(len(grid), 1)
        self.assertAlmostEqual(mat["000001"][0], 1.01 ** 4 - 1)

    def test_missing_week_zero_filled(self):
        rising = lambda d: 10000 + 100 * d
        a = self._bars([1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19,
                        29, 30, 31], rising, ticker="A")
        b = self._bars([1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19,
                        22, 23, 24, 25, 26, 29, 30, 31], rising, ticker="B")
        grid, mat = build_weekly_matrix(a + b, weeks=5)
        self.assertEqual(len(grid), 5)
        self.assertEqual(len(mat["A"]), 5)
        self.assertAlmostEqual(mat["A"][3], 0.0)  # W04 결측 → 0 채움
        self.assertGreater(mat["A"][0], 0.0)

    def test_low_coverage_excluded(self):
        a = self._bars(range(1, 6), lambda d: 10000, ticker="A")
        b = self._bars([1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 15, 16, 17, 18, 19,
                        22, 23, 24, 25, 26, 29, 30, 31], lambda d: 10000,
                       ticker="B")
        _, mat = build_weekly_matrix(a + b, weeks=5)
        self.assertNotIn("A", mat)
        self.assertIn("B", mat)


if __name__ == "__main__":
    unittest.main()
