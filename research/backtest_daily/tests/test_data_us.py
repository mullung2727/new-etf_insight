"""data_us 단위 테스트 — 임시 duckdb 파일만, 실 DB·네트워크 없음 (unittest)."""
import tempfile
import unittest
from pathlib import Path

import duckdb
import pandas as pd

from research.backtest_daily.data_us import (
    load_etf_tr, load_first_release, load_fred, load_index,
    synth_cash, synth_leveraged,
)


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
            con.execute(
                "CREATE TABLE fred_first_release (series_id VARCHAR, date VARCHAR,"
                " value DOUBLE, realtime_start VARCHAR, backfilled BOOLEAN,"
                " fetched_at TIMESTAMP)"
            )
            con.execute(
                "INSERT INTO fred_first_release VALUES ('UNRATE','20240201',3.9,"
                " '20240308', FALSE, TIMESTAMP '2024-03-09 00:00:00'),"
                " ('UNRATE','20240101',3.7,'20240202', FALSE,"
                " TIMESTAMP '2024-02-03 00:00:00')"
            )
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

    def test_d4_middle_gap_yields_nan_pair(self):
        """중간 결측일은 그날·다음날 수익 둘 다 NaN (여러 날 수익이 하루에 섞이지 않음)."""
        tmp = tempfile.TemporaryDirectory()
        try:
            db = Path(tmp.name) / "gap.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute("CREATE TABLE etf_ohlcv (date VARCHAR, ticker VARCHAR, close DOUBLE)")
                con.execute(
                    "INSERT INTO etf_ohlcv VALUES ('20240102','A',100), ('20240103','A',101),"
                    " ('20240104','A',102), ('20240102','B',50), ('20240104','B',55)"
                )
                con.execute("CREATE TABLE etf_dividends (ticker VARCHAR, date VARCHAR, amount DOUBLE)")
            finally:
                con.close()
            tr = load_etf_tr(["A", "B"], db=db)
            self.assertEqual(tr.index.tolist(), ["20240102", "20240103", "20240104"])
            self.assertTrue(pd.isna(tr.loc["20240103", "B"]))
            self.assertTrue(pd.isna(tr.loc["20240104", "B"]))
            self.assertAlmostEqual(tr.loc["20240103", "A"], 0.01)
            self.assertAlmostEqual(tr.loc["20240104", "A"], 102 / 101 - 1)
        finally:
            tmp.cleanup()

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

    def test_m5_first_release_columns_and_sort(self):
        df = load_first_release("UNRATE", db=self.db)
        self.assertEqual(df.columns.tolist(),
                         ["date", "value", "realtime_start", "backfilled"])
        self.assertEqual(df["date"].tolist(), ["20240101", "20240201"])
        self.assertAlmostEqual(df["value"].iloc[0], 3.7)
        self.assertEqual(df["realtime_start"].iloc[1], "20240308")
        self.assertEqual(df["backfilled"].tolist(), [False, False])
        with self.assertRaises(ValueError):
            load_first_release("NOPE", db=self.db)


class TestSynth(unittest.TestCase):
    def test_s1_leveraged_basic_and_borrow(self):
        idx = ["20240102", "20240103"]
        base = pd.Series([0.01, 0.01], index=idx)
        out = synth_leveraged(base, pd.Series([0.0, 0.0], index=idx),
                              leverage=3.0, fee_annual=0.0)
        for d in idx:
            self.assertAlmostEqual(out.loc[d], 0.03)
        out5 = synth_leveraged(base, pd.Series([5.0, 5.0], index=idx),
                               leverage=3.0, fee_annual=0.0)
        for d in idx:
            self.assertAlmostEqual(out5.loc[d], 0.03 - 2 * 0.05 / 252)

    def test_s2_rate_ffill(self):
        idx = ["20240102", "20240103", "20240104"]
        base = pd.Series([0.01, 0.01, 0.01], index=idx)
        rate = pd.Series([5.0], index=["20240102"])  # 이후 결측 → 직전값
        out = synth_leveraged(base, rate, leverage=3.0, fee_annual=0.0)
        for d in idx:
            self.assertAlmostEqual(out.loc[d], 0.03 - 2 * 0.05 / 252)

    def test_s3_cash(self):
        out = synth_cash(pd.Series([5.0, 5.0], index=["20240102", "20240103"]),
                         ["20240102", "20240103"])
        for d in ("20240102", "20240103"):
            self.assertAlmostEqual(out.loc[d], 0.05 / 252)


if __name__ == "__main__":
    unittest.main()
