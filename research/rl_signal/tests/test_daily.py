"""daily 테스트 (합성 px만, 실 DB 읽지 않음)."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.rl_signal.daily import DAILY_COLS, _shifted, daily_table, for_day


def _stock_px(n=30, ticker="000000"):
    dates = pd.date_range("2026-01-01", periods=n).strftime("%Y%m%d").tolist()
    close = np.arange(100, 100 + n, dtype=float)
    return pd.DataFrame({"date": dates, "ticker": ticker, "ms": np.arange(n),
                         "close": close, "market_cap": close * 10000,
                         "cmp_prev": np.ones(n)})


def _etf_px(n=30, ticker="069500"):
    dates = pd.date_range("2026-01-01", periods=n).strftime("%Y%m%d").tolist()
    close = np.arange(50, 50 + n, dtype=float)
    return pd.DataFrame({"date": dates, "ticker": ticker, "close": close, "ms": np.arange(n)})


class TestDailyTable(unittest.TestCase):
    def test_no_leak(self):
        px, etf = _stock_px(), _etf_px()
        tbl = daily_table(px, etf)
        px2 = px.copy()
        # D 종가 변경 + cmp_prev 같이 조정(D 기준가 124 유지, 기업행위·클립 없음)
        px2.loc[25, "close"] = 130.0
        px2.loc[25, "cmp_prev"] = 6.0
        tbl2 = daily_table(px2, etf)
        d, d1 = px.loc[25, "date"], px.loc[26, "date"]
        r0 = tbl[tbl["date"] == d].iloc[0]
        r1 = tbl2[tbl2["date"] == d].iloc[0]
        for c in DAILY_COLS:
            self.assertTrue(np.isfinite(r0[c]), c)
            self.assertEqual(r0[c], r1[c], c)
        p0 = tbl[(tbl["date"] == d1) & (tbl["ticker"] == "000000")].iloc[0]["prev_close"]
        p1 = tbl2[(tbl2["date"] == d1) & (tbl2["ticker"] == "000000")].iloc[0]["prev_close"]
        self.assertEqual(p0, 125.0)
        self.assertEqual(p1, 125.0)  # 기준가 모드: D+1 기준가는 D 종가와 무관
        t0 = tbl[(tbl["date"] == d1) & (tbl["ticker"] == "000000")].iloc[0]["ret5"]
        t1 = tbl2[(tbl2["date"] == d1) & (tbl2["ticker"] == "000000")].iloc[0]["ret5"]
        self.assertNotEqual(t0, t1)  # D+1 지표는 D 수익률 변화를 반영

    def test_ret5_value(self):
        tbl = daily_table(_stock_px(), pd.DataFrame())
        s = tbl[tbl["ticker"] == "000000"].reset_index(drop=True)
        self.assertTrue(np.isnan(s.loc[5, "ret5"]))
        self.assertAlmostEqual(s.loc[6, "ret5"], 105 / 100 - 1, places=9)

    def test_vol20_window(self):
        tbl = daily_table(_stock_px(), pd.DataFrame())
        s = tbl[tbl["ticker"] == "000000"].reset_index(drop=True)
        self.assertTrue(np.isnan(s.loc[20, "vol20"]))
        self.assertTrue(np.isfinite(s.loc[21, "vol20"]))

    def test_etf_raw_close(self):
        tbl = daily_table(pd.DataFrame(), _etf_px())
        e = tbl[tbl["ticker"] == "069500"].reset_index(drop=True)
        self.assertEqual(e.loc[3, "prev_close"], 52.0)
        self.assertAlmostEqual(e.loc[25, "ma20_gap"], 74 / 64.5 - 1, places=9)

    def test_for_day(self):
        tbl = daily_table(_stock_px(), _etf_px())
        day = _stock_px().loc[25, "date"]
        d = for_day(tbl, day)
        self.assertEqual(set(d), {"000000", "069500"})
        row = tbl[(tbl["date"] == day) & (tbl["ticker"] == "000000")].iloc[0]
        for c in DAILY_COLS:
            self.assertTrue(np.isfinite(row[c]), c)
            self.assertEqual(d["000000"][c], float(row[c]), c)


class TestVolBoundary(unittest.TestCase):
    def test_no_cross_ticker(self):
        n = 25
        dates = pd.date_range("2026-01-01", periods=n).strftime("%Y%m%d").tolist()
        pxa = pd.DataFrame({"date": dates, "ticker": "AAA", "ms": np.arange(n),
                            "close": np.full(n, 100.0)})
        pxb = pd.DataFrame({"date": dates, "ticker": "BBB", "ms": np.arange(n),
                            "close": np.full(n, 100.0)})
        px = pd.concat([pxa, pxb], ignore_index=True)
        P = np.ones(2 * n)
        r = np.array([0.0] * n + [1.0] * n)
        tbl = _shifted(px, P, r)
        b = tbl[tbl["ticker"] == "BBB"].reset_index(drop=True)
        self.assertTrue(np.isnan(b.loc[1, "vol20"]))
        a = tbl[tbl["ticker"] == "AAA"].reset_index(drop=True)
        self.assertTrue(np.isnan(a.loc[1, "vol20"]))


class TestCmpMissing(unittest.TestCase):
    def test_nan_stays_nan(self):
        px = _stock_px()
        px.loc[15, "cmp_prev"] = np.nan
        tbl = daily_table(px, pd.DataFrame())
        s = tbl[tbl["ticker"] == "000000"].reset_index(drop=True)
        self.assertTrue(np.isnan(s.loc[21, "vol20"]))
        full = daily_table(_stock_px(), pd.DataFrame())
        f = full[full["ticker"] == "000000"].reset_index(drop=True)
        self.assertTrue(np.isfinite(f.loc[21, "vol20"]))


class TestPrevCloseReference(unittest.TestCase):
    def test_stock_uses_reference(self):
        dates = pd.date_range("2026-01-01", periods=5).strftime("%Y%m%d").tolist()
        close = np.array([100.0, 102.0, 101.0, 103.0, 105.0])
        cmp_prev = np.array([0.0, 2.0, np.nan, 1.0, 0.0])
        px = pd.DataFrame({"date": dates, "ticker": "000000", "ms": np.arange(5),
                           "close": close, "cmp_prev": cmp_prev,
                           "market_cap": close * 1000})
        tbl = daily_table(px, pd.DataFrame())
        s = tbl[tbl["ticker"] == "000000"].reset_index(drop=True)
        self.assertAlmostEqual(s.loc[0, "prev_close"], 100.0)
        self.assertAlmostEqual(s.loc[1, "prev_close"], 100.0)
        self.assertAlmostEqual(s.loc[2, "prev_close"], 102.0)
        self.assertAlmostEqual(s.loc[3, "prev_close"], 102.0)
        self.assertAlmostEqual(s.loc[4, "prev_close"], 105.0)


if __name__ == "__main__":
    unittest.main()
