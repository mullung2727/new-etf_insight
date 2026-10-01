"""fills 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import numpy as np

from research.backtest_minute.fills import daily_buy_fill, limit_buy_fill
from research.backtest_minute.ticks import tick_below


def _bars(times, lows, opens=None):
    n = len(times)
    return {"time": np.array(times, dtype=int),
            "open": (np.array(opens, dtype=float) if opens is not None
                     else np.full(n, 10000.0)),
            "high": np.full(n, 10050.0),
            "low": np.array(lows, dtype=float),
            "close": np.full(n, 10000.0),
            "volume": np.ones(n)}


class TestLimitBuyFill(unittest.TestCase):
    def test_touch_only_no_fill(self):
        b = _bars([90000, 90100], [10000.0, 10000.0])
        self.assertIsNone(limit_buy_fill(b, 10000, 10000, pre_open=False))

    def test_one_tick_below_fills(self):
        px = 10000
        thr = px - tick_below(px)
        b = _bars([90000, 90100], [10000.0, thr])
        self.assertEqual(limit_buy_fill(b, px, 10000, pre_open=False),
                         (1, float(px)))

    def test_outside_window(self):
        px = 10000
        thr = px - tick_below(px)
        b = _bars([90000, 90100, 90200], [thr, thr, thr])
        self.assertIsNone(limit_buy_fill(b, px, 10000, pre_open=False,
                                         after=90300))
        self.assertIsNone(limit_buy_fill(b, px, 10000, pre_open=False,
                                         before=90000))
        self.assertEqual(limit_buy_fill(b, px, 10000, pre_open=False,
                                        after=90100, before=90200),
                         (1, float(px)))

    def test_below_lower_no_order(self):
        b = _bars([90000], [6000.0])
        self.assertIsNone(limit_buy_fill(b, 6999, 10000, pre_open=False))
        self.assertIsNone(limit_buy_fill(b, 13001, 10000, pre_open=False))
        self.assertIsNone(limit_buy_fill(b, 6999, 10000, pre_open=True))
        self.assertIsNone(limit_buy_fill(b, 13001, 10000, pre_open=True))

    def test_none_bars(self):
        self.assertIsNone(limit_buy_fill(None, 10000, 10000, pre_open=False))

    def test_bad_pc_no_fill(self):
        b = _bars([90000], [9000.0])
        self.assertIsNone(limit_buy_fill(b, 10000, float("nan"),
                                         pre_open=False))
        self.assertIsNone(limit_buy_fill(b, 10000, 0, pre_open=False))

    def test_pre_open_missing(self):
        b = _bars([90000], [9000.0])
        with self.assertRaises(TypeError):
            limit_buy_fill(b, 10000, 10000)

    def test_preopen_open_equals_price(self):
        b = _bars([90000, 90100], [10000.0, 10000.0],
                  opens=[10000.0, 10000.0])
        self.assertEqual(limit_buy_fill(b, 10000, 10000, pre_open=True),
                         (0, 10000.0))

    def test_preopen_open_below_price(self):
        b = _bars([90000, 90100], [9900.0, 9900.0], opens=[9900.0, 9900.0])
        self.assertEqual(limit_buy_fill(b, 10000, 10000, pre_open=True),
                         (0, 9900.0))

    def test_preopen_open_above_falls_through(self):
        px = 10000
        thr = px - tick_below(px)
        b = _bars([90000, 90100], [10100.0, thr],
                  opens=[10100.0, 10100.0])
        self.assertEqual(limit_buy_fill(b, px, 10000, pre_open=True),
                         (1, float(px)))
        b2 = _bars([90000, 90100], [10100.0, px], opens=[10100.0, 10100.0])
        self.assertIsNone(limit_buy_fill(b2, px, 10000, pre_open=True))

    def test_no_preopen_open_equals_needs_tick(self):
        b = _bars([90000, 90100], [10000.0, 10000.0],
                  opens=[10000.0, 10000.0])
        self.assertIsNone(limit_buy_fill(b, 10000, 10000, pre_open=False))

    def test_preopen_first_bar_not_0900(self):
        b = _bars([90100, 90200], [9900.0, 9900.0], opens=[9900.0, 9900.0])
        self.assertEqual(limit_buy_fill(b, 10000, 10000, pre_open=True),
                         (0, 10000.0))


class TestDailyBuyFill(unittest.TestCase):
    def test_threshold(self):
        px = 10000
        thr = px - tick_below(px)
        self.assertEqual(daily_buy_fill(10000.0, thr, px, pre_open=False),
                         float(px))
        self.assertIsNone(daily_buy_fill(10000.0, thr + 1, px,
                                         pre_open=False))

    def test_preopen_open_equals_price(self):
        self.assertEqual(daily_buy_fill(10000.0, 10000.0, 10000,
                                        pre_open=True), 10000.0)

    def test_preopen_open_below_price(self):
        self.assertEqual(daily_buy_fill(9900.0, 9900.0, 10000, pre_open=True),
                         9900.0)

    def test_preopen_open_above_falls_through(self):
        px = 10000
        thr = px - tick_below(px)
        self.assertEqual(daily_buy_fill(10100.0, thr, px, pre_open=True),
                         float(px))
        self.assertIsNone(daily_buy_fill(10100.0, px, px, pre_open=True))

    def test_no_preopen_open_equals_needs_tick(self):
        self.assertIsNone(daily_buy_fill(10000.0, 10000.0, 10000,
                                         pre_open=False))

    def test_pre_open_missing(self):
        with self.assertRaises(TypeError):
            daily_buy_fill(10000.0, 9000.0, 10000)

    def test_bad_input(self):
        self.assertIsNone(daily_buy_fill(10000.0, float("nan"), 10000,
                                         pre_open=False))
        self.assertIsNone(daily_buy_fill(10000.0, 9000.0, float("nan"),
                                         pre_open=False))


if __name__ == "__main__":
    unittest.main()
