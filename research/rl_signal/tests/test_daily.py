"""daily 테스트 (합성 px만, 실 DB 읽지 않음)."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.rl_signal.daily import DAILY_COLS, daily_table, for_day


def _stock_px(n=30, ticker="000000"):
    dates = pd.date_range("2026-01-01", periods=n).strftime("%Y%m%d").tolist()
    close = np.arange(100, 100 + n, dtype=float)
    return pd.DataFrame({"date": dates, "ticker": ticker, "ms": np.arange(n),
                         "close": close, "market_cap": close * 10000})


def _etf_px(n=30, ticker="069500"):
    dates = pd.date_range("2026-01-01", periods=n).strftime("%Y%m%d").tolist()
    close = np.arange(50, 50 + n, dtype=float)
    return pd.DataFrame({"date": dates, "ticker": ticker, "close": close, "ms": np.arange(n)})


class TestDailyTable(unittest.TestCase):
    def test_no_leak(self):
        px, etf = _stock_px(), _etf_px()
        tbl = daily_table(px, etf)
        px2 = px.copy()
        px2.loc[25, "close"] = 130.0  # D 종가 변경(소폭, 기업행위·클립 없음)
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
        self.assertEqual(p1, 130.0)

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


if __name__ == "__main__":
    unittest.main()
