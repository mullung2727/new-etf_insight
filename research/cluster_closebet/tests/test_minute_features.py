"""분봉 LM feature TDD — lm_prices.

실행: repo root에서
`etl\\.venv\\Scripts\\python.exe -m unittest research.cluster_closebet.tests.test_minute_features -v`
"""
import unittest

from research.cluster_closebet.minute_features import lm_prices


def _bar(time, close):
    return {"time": time, "close": close}


class TestLmPrices(unittest.TestCase):
    def test_p1520_is_last_bar_before_1520(self):
        bars = [
            _bar("085900", 1),  # 장 시작 전 → 제외
            _bar("142900", 99),
            _bar("145900", 101),
            _bar("150000", 102),
            _bar("151900", 105),
            _bar("153000", 200),  # 1520 이후 → 제외
        ]
        self.assertEqual(lm_prices(bars),
                         {"p1430": 99, "p1500": 101, "p1520": 105})

    def test_missing_1430_is_none(self):
        bars = [_bar("143000", 100), _bar("151900", 105)]
        self.assertIsNone(lm_prices(bars))

    def test_empty_is_none(self):
        self.assertIsNone(lm_prices([]))


if __name__ == "__main__":
    unittest.main()
