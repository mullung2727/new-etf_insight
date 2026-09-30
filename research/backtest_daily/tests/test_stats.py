"""stats 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import numpy as np

from research.backtest_daily.stats import day_key, month_key, weighted


class TestWeighted(unittest.TestCase):
    def test_groups(self):
        mean, t, nk, n = weighted([1, 1, 1, 3], ["A", "A", "A", "B"])
        self.assertAlmostEqual(mean, 2.0)
        self.assertEqual(nk, 2)
        self.assertEqual(n, 4)

    def test_single_key_t_nan(self):
        mean, t, nk, n = weighted([1, 1, 1], ["A", "A", "A"])
        self.assertAlmostEqual(mean, 1.0)
        self.assertTrue(np.isnan(t))
        self.assertEqual(nk, 1)


class TestKeys(unittest.TestCase):
    def test_day_month(self):
        self.assertEqual(day_key(["20240115"]), ["20240115"])
        self.assertEqual(month_key(["20240115", "20240201"]), ["202401", "202402"])


if __name__ == "__main__":
    unittest.main()
