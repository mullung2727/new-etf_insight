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


def _px_krx(rows):
    return pd.DataFrame(rows, columns=["ticker", "ms", "close", "cmp_prev"])


class TestAdjReturnsKrxReference(unittest.TestCase):
    def test_split_2to1_zero(self):
        # 100 -> 50 분할, 기준가 50 (cmp=0) → 0%
        rows = [("A", i, c, 0.0) for i, c in enumerate([100.0, 100.0, 50.0, 50.0])]
        r = adj_returns(_px_krx(rows), use_krx_reference=True)
        self.assertTrue(np.isnan(r[0]))
        for i in (1, 2, 3):
            self.assertAlmostEqual(r[i], 0.0, places=9)

    def test_rights_issue_zero(self):
        # 100 -> 80 권리락, 기준가 80 (cmp=0) → 0%
        rows = [("A", 0, 100.0, 0.0), ("A", 1, 80.0, 0.0), ("A", 2, 80.0, 0.0)]
        r = adj_returns(_px_krx(rows), use_krx_reference=True)
        self.assertAlmostEqual(r[1], 0.0, places=9)
        self.assertAlmostEqual(r[2], 0.0, places=9)

    def test_ordinary_return(self):
        # close 82, cmp 2 → 82/80 - 1 = 2.5%
        rows = [("A", 0, 80.0, 0.0), ("A", 1, 82.0, 2.0)]
        r = adj_returns(_px_krx(rows), use_krx_reference=True)
        self.assertAlmostEqual(r[1], 0.025, places=9)

    def test_boundary_gap_nan(self):
        a = [("A", i, 100.0 + i, 0.0) for i in range(3)]
        b = [("B", i, 50.0, 0.0) for i in range(2)]
        r = adj_returns(_px_krx(a + b), use_krx_reference=True)
        self.assertTrue(np.isnan(r[0]))
        self.assertTrue(np.isnan(r[3]))  # 종목 경계
        self.assertAlmostEqual(r[4], 0.0, places=9)
        gap = [("A", ms, 100.0, 0.0) for ms in [0, 1, 2, 5, 6]]
        g = adj_returns(_px_krx(gap), use_krx_reference=True)
        self.assertTrue(np.isnan(g[3]))  # ms 비연속
        self.assertAlmostEqual(g[4], 0.0, places=9)

    def test_missing_invalid_nan(self):
        rows = [("A", 0, 100.0, 0.0),
                ("A", 1, 100.0, np.nan),   # cmp 결측
                ("A", 2, 50.0, 60.0),      # ref = -10 <= 0
                ("A", 3, 50.0, 50.0),      # ref = 0
                ("A", 4, 0.0, 0.0),        # close 무효
                ("A", 5, 55.0, 5.0)]       # 55/50-1 = 10% (정상 대조)
        r = adj_returns(_px_krx(rows), use_krx_reference=True)
        for i in (1, 2, 3, 4):
            self.assertTrue(np.isnan(r[i]), i)
        self.assertAlmostEqual(r[5], 0.10, places=9)

    def test_missing_column_raises(self):
        with self.assertRaises(ValueError):
            adj_returns(_px([("A", 0, 100.0, 1e10)]), use_krx_reference=True)

    def test_default_false_ignores_cmp(self):
        # legacy 2:1 분할 데이터에 cmp 를 일부러 다르게 넣어도 기본값은 기존 결과와 동일
        rows = [("A", i, 100.0 if i < 5 else 50.0, 1e10) for i in range(10)]
        px = _px(rows)
        px["cmp_prev"] = 7.0  # 신모드면 0이 아닌 값이 나오는 방해값
        r_default = adj_returns(px)
        r_legacy = adj_returns(_px(rows))
        self.assertTrue(np.array_equal(r_default, r_legacy, equal_nan=True))
        r_new = adj_returns(px, use_krx_reference=True)
        self.assertFalse(np.array_equal(r_default, r_new, equal_nan=True))

    def test_no_cross_ticker_fill(self):
        rows = [("A", 0, 100.0, 0.0), ("A", 1, 100.0, 0.0),
                ("B", 0, 50.0, 0.0), ("B", 1, 55.0, 5.0)]
        r = adj_returns(_px_krx(rows), use_krx_reference=True)
        self.assertTrue(np.isnan(r[2]))
        self.assertAlmostEqual(r[3], 0.10, places=9)  # B 내부 ref만 사용


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
