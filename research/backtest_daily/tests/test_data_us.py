"""data_us 단위 테스트 — 임시 duckdb 파일만, 실 DB·네트워크 없음 (unittest)."""
import tempfile
import unittest
from pathlib import Path

import duckdb
import pandas as pd

from research.backtest_daily.data_us import load_etf_tr, load_fred, load_index


class TestDataUs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t.duckdb"
        con = duckdb.connect(str(self.db))
        try:
            con.execute("CREATE TABLE etf_ohlcv (date VARCHAR, ticker VARCHAR, close DOUBLE)")
            con.execute(
                "INSERT INTO etf_ohlcv VALUES ('20240102','TQQQ',100), ('20240103','TQQQ',99),"
                " ('20240102','QQQ',10), ('20240103','QQQ',11)"
            )
            con.execute("CREATE TABLE etf_dividends (ticker VARCHAR, date VARCHAR, amount DOUBLE)")
            con.execute("INSERT INTO etf_dividends VALUES ('TQQQ','20240103',2)")
            con.execute(
                "CREATE TABLE fred_obs (series_id VARCHAR, date VARCHAR,"
                " value DOUBLE, fetched_at TIMESTAMP)"
            )
            con.execute(
                "INSERT INTO fred_obs VALUES ('BAMLH0A0HYM2','20240102',3.0,"
                " TIMESTAMP '2024-01-03 00:00:00'), ('BAMLH0A0HYM2','20240102',3.1,"
                " TIMESTAMP '2024-01-04 00:00:00')"
            )
            con.execute("CREATE TABLE index_ohlcv (date VARCHAR, ticker VARCHAR, close DOUBLE)")
            con.execute("INSERT INTO index_ohlcv VALUES ('20240102','^VIX',13.5), ('20240103','^VIX',14.0)")
        finally:
            con.close()

    def tearDown(self):
        self._tmp.cleanup()

    def test_d1_total_return(self):
        tr = load_etf_tr(["QQQ", "TQQQ"], db=self.db)
        self.assertEqual(tr.columns.tolist(), ["QQQ", "TQQQ"])
        self.assertEqual(tr.index.tolist(), ["20240102", "20240103"])
        self.assertTrue(pd.isna(tr.loc["20240102", "TQQQ"]))
        self.assertAlmostEqual(tr.loc["20240103", "TQQQ"], 0.01)
        self.assertAlmostEqual(tr.loc["20240103", "QQQ"], 0.10)

    def test_d2_fred_as_of(self):
        s = load_fred("BAMLH0A0HYM2", db=self.db)
        self.assertEqual(s.name, "BAMLH0A0HYM2")
        self.assertAlmostEqual(s.loc["20240102"], 3.1)
        old = load_fred("BAMLH0A0HYM2", as_of="2024-01-03 00:00:00", db=self.db)
        self.assertAlmostEqual(old.loc["20240102"], 3.0)

    def test_d3_unknown(self):
        with self.assertRaises(ValueError):
            load_etf_tr(["XXXX"], db=self.db)
        with self.assertRaises(ValueError):
            load_fred("NOPE", db=self.db)
        with self.assertRaises(ValueError):
            load_index("NOPE", db=self.db)

    def test_index(self):
        s = load_index("^VIX", db=self.db)
        self.assertEqual(s.name, "^VIX")
        self.assertEqual(s.index.tolist(), ["20240102", "20240103"])
        self.assertAlmostEqual(s.loc["20240103"], 14.0)


if __name__ == "__main__":
    unittest.main()
