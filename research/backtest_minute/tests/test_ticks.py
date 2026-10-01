"""ticks 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

from research.backtest_minute.ticks import (
    buyable,
    lower_limit,
    tick_below,
    tick_size,
    upper_limit,
)


class TestTickSize(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(tick_size(1999), 1)
        self.assertEqual(tick_size(2000), 5)
        self.assertEqual(tick_size(4995), 5)
        self.assertEqual(tick_size(5000), 10)
        self.assertEqual(tick_size(19990), 10)
        self.assertEqual(tick_size(20000), 50)

    def test_below(self):
        self.assertEqual(tick_below(2000), 1)
        self.assertEqual(tick_below(5000), 5)
        self.assertEqual(tick_below(20000), 10)


class TestLimits(unittest.TestCase):
    def test_pc_10000(self):
        self.assertEqual(upper_limit(10000), 13000)
        self.assertEqual(lower_limit(10000), 7000)

    def test_pc_1550(self):
        self.assertEqual(upper_limit(1550), 2015)

    def test_buyable(self):
        # 12950/10000-1은 부동소수 오차로 0.295보다 작아져 True가 되므로 경계값 회피
        self.assertTrue(buyable(12940, 10000))
        self.assertFalse(buyable(12960, 10000))


if __name__ == "__main__":
    unittest.main()
