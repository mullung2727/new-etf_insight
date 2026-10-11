"""합성 데이터로 returns 검증 (C16,C17)."""

import unittest

import duckdb
import pandas as pd

from research.ccc.returns import month_end_prices, monthly_returns


class TestReturns(unittest.TestCase):
    def test_c16_prev_raw_close_consecutive_only(self) -> None:
        px = pd.DataFrame([
            {"ticker": "AAA", "ym": 202001, "close": 25.0, "raw_close": 100.0, "market": "NYSE"},
            {"ticker": "AAA", "ym": 202002, "close": 30.0, "raw_close": 30.0, "market": "NYSE"},
            {"ticker": "AAA", "ym": 202004, "close": 33.0, "raw_close": 33.0, "market": "NYSE"},
        ])
        out = monthly_returns(px)
        self.assertIn("prev_raw_close", out.columns)
        o = out.set_index("ym").to_dict("index")
        self.assertTrue(pd.isna(o[202001]["prev_raw_close"]))
        self.assertTrue(pd.isna(o[202001]["ret"]))
        self.assertAlmostEqual(o[202002]["prev_close"], 25.0)
        self.assertAlmostEqual(o[202002]["prev_raw_close"], 100.0)
        self.assertAlmostEqual(o[202002]["ret"], 0.2)
        self.assertTrue(pd.isna(o[202004]["prev_raw_close"]))
        self.assertTrue(pd.isna(o[202004]["prev_close"]))
        self.assertTrue(pd.isna(o[202004]["ret"]))

    def test_c17_month_end_raw_close_split(self) -> None:
        con = duckdb.connect()
        try:
            con.execute("CREATE TABLE ohlcv (date VARCHAR, ticker VARCHAR, market VARCHAR, close DOUBLE)")
            con.execute("CREATE TABLE splits (ticker VARCHAR, date VARCHAR, ratio DOUBLE)")
            con.execute("""INSERT INTO ohlcv VALUES
                ('20200730', 'AAA', 'NASDAQ', 100.0),
                ('20200731', 'AAA', 'NASDAQ', 110.0),
                ('20200831', 'AAA', 'NASDAQ', 120.0),
                ('20200930', 'AAA', 'NASDAQ', 30.0)""")
            con.execute("INSERT INTO splits VALUES ('AAA', '20200831', 4.0)")
            out = month_end_prices(con=con)
            con.execute("SELECT 1").fetchone()
            o = out.set_index("ym").to_dict("index")
            self.assertAlmostEqual(o[202007]["close"], 110.0)
            self.assertAlmostEqual(o[202007]["raw_close"], 440.0)
            self.assertAlmostEqual(o[202009]["close"], 30.0)
            self.assertAlmostEqual(o[202009]["raw_close"], 30.0)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
