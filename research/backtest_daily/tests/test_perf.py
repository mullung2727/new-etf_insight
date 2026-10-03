"""perf 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import math
import statistics
import unittest

import pandas as pd

from research.backtest_daily import perf


def _s(xs, start=20240102):
    idx = [f"{start + i:08d}" for i in range(len(xs))]
    return pd.Series(list(xs), index=idx, dtype=float)


class TestPerf(unittest.TestCase):
    def test_f1_cagr(self):
        r = 1.21 ** (1 / 252) - 1
        ret = _s([r] * 252)
        self.assertAlmostEqual(perf.cagr(ret), 0.21)

    def test_f2_max_drawdown_from_start(self):
        self.assertAlmostEqual(perf.max_drawdown(_s([-0.10, 0.05])), -0.10)
        self.assertAlmostEqual(perf.max_drawdown(_s([0.10, -0.20, 0.30])), -0.20)

    def test_f3_drawdowns(self):
        eq = [1.0, 1.1, 0.88, 1.2, 1.08]
        ret = _s([eq[i] / eq[i - 1] - 1 if i else 0.0 for i in range(5)])
        dd = perf.drawdowns(ret)
        self.assertAlmostEqual(dd["depth"].iloc[0], -0.20)
        self.assertEqual(dd["peak"].iloc[0], ret.index[1])
        self.assertEqual(dd["recovery"].iloc[0], ret.index[3])
        self.assertAlmostEqual(dd["depth"].iloc[1], -0.10)
        self.assertIsNone(dd["recovery"].iloc[1])

    def test_f4_sharpe(self):
        self.assertTrue(math.isnan(perf.sharpe(_s([0.01] * 4))))
        xs = [0.01, -0.01, 0.02, 0.0]
        expected = statistics.mean(xs) / statistics.stdev(xs) * math.sqrt(252)
        self.assertAlmostEqual(perf.sharpe(_s(xs)), expected)

    def test_f5_yearly(self):
        ret = pd.Series([0.1, 0.1, -0.1],
                        index=["20240102", "20240203", "20250104"], dtype=float)
        y = perf.yearly(ret)
        self.assertAlmostEqual(y["2024"], 0.21)
        self.assertAlmostEqual(y["2025"], -0.10)

    def test_f6_trades_per_year(self):
        t = pd.Series([0.0] * 504)
        t.iloc[[0, 100, 200, 300]] = 0.5
        self.assertAlmostEqual(perf.trades_per_year(t), 2.0)

    def test_f7_nan_raises(self):
        bad = _s([0.01, float("nan")])
        for fn in (perf.equity, perf.cagr, perf.vol, perf.sharpe,
                   perf.max_drawdown, perf.calmar, perf.yearly,
                   perf.drawdowns, perf.summary):
            with self.subTest(fn=fn.__name__), self.assertRaises(ValueError):
                fn(bad)


if __name__ == "__main__":
    unittest.main()
