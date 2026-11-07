"""costs 테스트 (합성, 실파일 없음)."""

from __future__ import annotations

import unittest

from research.rl_signal.costs import FEE, SELL_TAX, buy_cost, sell_cost


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


if __name__ == "__main__":
    unittest.main()
