"""costs 테스트 (합성, 실파일 없음)."""

from __future__ import annotations

import unittest

from research.backtest_minute.ticks import tick_size
from research.rl_signal.costs import FEE, SELL_TAX, UNIT_KRW, buy_cost, sell_cost, tick


class TestCosts(unittest.TestCase):
    def test_buy_cost(self):
        self.assertEqual(FEE, 0.00015)
        self.assertAlmostEqual(buy_cost("069500"), 0.00015)
        self.assertAlmostEqual(buy_cost("005930"), 0.00015)

    def test_sell_cost_etf(self):
        self.assertAlmostEqual(sell_cost("069500"), 0.00015)

    def test_sell_cost_stock(self):
        self.assertEqual(SELL_TAX, 0.0020)
        self.assertAlmostEqual(sell_cost("005930"), 0.00215)

    def test_unit_krw(self):
        self.assertEqual(UNIT_KRW, 1_000_000)

    def test_tick_etf(self):
        self.assertEqual(tick("069500", 1999), 1)
        self.assertEqual(tick("069500", 2000), 5)
        self.assertEqual(tick("069500", 1500.5), 1)
        self.assertEqual(tick("122630", 5000), 5)

    def test_tick_stock(self):
        self.assertEqual(tick("005930", 50000), 100)
        for px in (1000, 1999, 2000, 3000, 4999, 5000, 10000, 19999, 20000,
                   30000, 49999, 50000, 100000, 199999, 200000, 300000, 600000):
            self.assertEqual(tick("005930", px), tick_size(px), px)


if __name__ == "__main__":
    unittest.main()
