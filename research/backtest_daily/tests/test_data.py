"""data 단위 테스트 — cmp_prev 없는 DB도 load_px 동작 (unittest)."""
import tempfile
import unittest
from pathlib import Path

import duckdb

from research.backtest_daily.data import load_px


class TestLoadPxWithoutCmpPrev(unittest.TestCase):
    def test_missing_cmp_prev_is_all_nan(self):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "px.duckdb")
            con = duckdb.connect(db)
            try:
                con.execute(
                    """
                    CREATE TABLE ohlcv (
                        date VARCHAR, ticker VARCHAR, market VARCHAR,
                        open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                        volume BIGINT, trading_value DOUBLE, market_cap DOUBLE
                    )
                    """
                )
                con.execute(
                    "INSERT INTO ohlcv VALUES "
                    "('20240102', '005930', 'KOSPI', 100, 101, 99, 100, 10, 1000, 100000),"
                    "('20240103', '005930', 'KOSPI', 100, 102, 99, 101, 20, 2000, 100000)"
                )
            finally:
                con.close()
            px = load_px(db=db)
        self.assertIn("cmp_prev", px.columns)
        self.assertTrue(px["cmp_prev"].isna().all())
        self.assertEqual(len(px), 2)


if __name__ == "__main__":
    unittest.main()
