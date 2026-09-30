"""adjust 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import numpy as np
import pandas as pd

from research.backtest_daily.adjust import adj_returns, ma_n


def _px(rows):
    return pd.DataFrame(rows, columns=["ticker", "ms", "close", "market_cap"])


class TestAdjReturns(unittest.TestCase):
    def test_split_2to1(self):
        rows = [("A", i, 100.0 if i < 5 else 50.0, 1e10) for i in range(10)]
        r = adj_returns(_px(rows))
        self.assertTrue(np.isnan(r[0]))
        for i in (1, 2, 3, 4, 6, 7, 8, 9):
            self.assertAlmostEqual(r[i], 0.0, places=9)
        self.assertLess(abs(r[5]), 0.01)

    def test_bonus_issue_20pct(self):
        rows = []
        for i in range(12):
            if i < 5:
                c, cap = 120.0, 12000.0
            elif i < 10:
                c, cap = 100.0, 10000.0
            else:
                c, cap = 100.0, 12000.0
            rows.append(("A", i, c, cap))
        r = adj_returns(_px(rows))
        self.assertLess(abs(r[5]), 0.01)
        self.assertAlmostEqual(r[10], 0.0, places=9)

    def test_ticker_boundary(self):
        a = [("A", i, 100.0, 1e10 if i < 3 else 2e10) for i in range(6)]
        b = [("B", i, c, cap) for i, (c, cap) in
             enumerate([(100.0, 1e10), (100.0, 1e10),
                        (50.0, 0.5e10), (50.0, 0.5e10)])]
        r = adj_returns(_px(a + b))
        self.assertAlmostEqual(r[3], 0.0, places=9)
        self.assertTrue(np.isnan(r[6]))
        self.assertAlmostEqual(r[8], -0.30, places=6)

    def test_ms_gap_nan(self):
        rows = [("A", ms, 100.0 + i, 1e10)
                for i, ms in enumerate([0, 1, 2, 5, 6])]
        r = adj_returns(_px(rows))
        self.assertTrue(np.isnan(r[0]))
        self.assertTrue(np.isfinite(r[1]) and np.isfinite(r[2]))
        self.assertTrue(np.isnan(r[3]))
        self.assertTrue(np.isfinite(r[4]))


class TestMaN(unittest.TestCase):
    def test_default_n20_flat(self):
        got = ma_n(list(range(21)), [1.0] * 21)
        self.assertTrue(np.isnan(got[:19]).all())
        self.assertAlmostEqual(got[19], 1.0)
        self.assertAlmostEqual(got[-1], 1.0)

    def test_n20_ramp(self):
        # 1..20 평균 = 10.5
        got = ma_n(list(range(20)), [float(i) for i in range(1, 21)])
        self.assertTrue(np.isnan(got[:-1]).all())
        self.assertAlmostEqual(got[-1], 10.5)

    def test_n5(self):
        got = ma_n([0, 1, 2, 3, 4], [1.0, 2.0, 3.0, 4.0, 5.0], n=5)
        self.assertTrue(np.isnan(got[:-1]).all())
        self.assertAlmostEqual(got[-1], 3.0)

    def test_gap_nan(self):
        got = ma_n(list(range(20)) + [21], [1.0] * 21)
        self.assertTrue(np.isnan(got[-1]))


if __name__ == "__main__":
    unittest.main()
