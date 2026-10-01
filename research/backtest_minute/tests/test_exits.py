"""exits 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import numpy as np

from research.backtest_minute.exits import tp_sl_exit


def _bars(times, opens, highs, lows, closes):
    return {"time": np.array(times, dtype=int),
            "open": np.array(opens, dtype=float),
            "high": np.array(highs, dtype=float),
            "low": np.array(lows, dtype=float),
            "close": np.array(closes, dtype=float),
            "volume": np.ones(len(times))}


def _day(date, pc, bars, o=10000.0, low=9900.0, c=10050.0):
    return {"date": date, "pc": pc, "bars": bars, "open": o, "low": low, "close": c}


class TestIntraday(unittest.TestCase):
    def test_gap_tp(self):
        b = _bars([90000, 90100], [10000, 10300], [10000, 10350],
                  [9900, 10250], [10000, 10300])
        r = tp_sl_exit([_day("20251202", 10000, b)], 0, 10000, tp_px=10200, sl_px=9500)
        self.assertEqual(r["reason"], "gap_tp")
        self.assertAlmostEqual(r["exit_px"], 10300.0)

    def test_gap_sl(self):
        b = _bars([90000, 90100], [10000, 9400], [10000, 9450],
                  [9900, 9350], [10000, 9400])
        r = tp_sl_exit([_day("20251202", 10000, b)], 0, 10000, tp_px=10200, sl_px=9500)
        self.assertEqual(r["reason"], "gap_sl")
        self.assertAlmostEqual(r["exit_px"], 9400.0)

    def test_both_sl(self):
        b = _bars([90000, 90100], [10000, 10000], [10000, 10300],
                  [9900, 9400], [10000, 9900])
        r = tp_sl_exit([_day("20251202", 10000, b)], 0, 10000, tp_px=10200, sl_px=9500)
        self.assertEqual(r["reason"], "both_sl")
        self.assertAlmostEqual(r["exit_px"], 9500.0)

    def test_entry_bar_ignored(self):
        b = _bars([90000, 90100, 153000], [10000, 10000, 10050],
                  [10500, 10100, 10100], [9900, 9900, 10000],
                  [10000, 10050, 10050])
        r = tp_sl_exit([_day("20251202", 10000, b)], 0, 10000, tp_px=10200, sl_px=9500)
        self.assertEqual(r["reason"], "close")
        self.assertAlmostEqual(r["exit_px"], 10050.0)

    def test_multi_day_close(self):
        b0 = _bars([90000, 153000], [10000, 10050], [10100, 10100],
                   [9900, 10000], [10050, 10080])
        b1 = _bars([90000, 153000], [10080, 10100], [10150, 10120],
                   [10050, 10090], [10100, 10110])
        days = [_day("20251202", 10000, b0), _day("20251203", 10080, b1),
                _day("20251204", 10110, None, o=10120.0, low=10100.0, c=10130.0)]
        r = tp_sl_exit(days, 0, 10000, tp_px=20000, sl_px=5000, hold_days=1)
        self.assertEqual(r["reason"], "close")
        self.assertEqual(r["exit_date"], "20251203")
        self.assertAlmostEqual(r["exit_px"], 10110.0)

    def test_time_exit(self):
        b = _bars([90000, 90100, 90200, 153000], [10000] * 4, [10050] * 4,
                  [9950] * 4, [10000, 10010, 10020, 10030])
        r = tp_sl_exit([_day("20251202", 10000, b)], 0, 10000, time_bars=2)
        self.assertEqual(r["reason"], "time")
        self.assertAlmostEqual(r["exit_px"], 10020.0)


class TestLowerHalt(unittest.TestCase):
    def test_ld_release(self):
        b = _bars([90000, 90100, 90200], [8000, 8000, 6900],
                  [8100, 8050, 7200], [7900, 6800, 6850],
                  [8000, 6900, 7100])
        nxt = _day("20251203", 6900, None, o=7100.0, low=6800.0, c=7200.0)
        r = tp_sl_exit([_day("20251202", 10000, b), nxt], 0, 8000, sl_px=6900)
        self.assertEqual(r["reason"], "ld_release")
        self.assertAlmostEqual(r["exit_px"], 7100.0)

    def test_ld_next_open(self):
        b = _bars([90000, 90100, 90200], [8000, 8000, 6900],
                  [8100, 8050, 6950], [7900, 6800, 6850],
                  [8000, 6900, 6950])
        nxt = _day("20251203", 6950, None, o=7100.0, low=6800.0, c=7200.0)
        r = tp_sl_exit([_day("20251202", 10000, b), nxt], 0, 8000, sl_px=6900)
        self.assertEqual(r["reason"], "ld_next_open")
        self.assertEqual(r["exit_date"], "20251203")
        self.assertAlmostEqual(r["exit_px"], 7100.0)

    def test_halt_next_open(self):
        b = _bars([90000, 140000], [10000, 10020], [10050, 10060],
                  [9950, 9990], [10020, 10030])
        nxt = _day("20251203", 10030, None, o=10100.0, low=10050.0, c=10120.0)
        r = tp_sl_exit([_day("20251202", 10000, b), nxt], 0, 10000,
                       tp_px=20000, sl_px=5000)
        self.assertEqual(r["reason"], "halt_next_open")
        self.assertAlmostEqual(r["exit_px"], 10100.0)


class TestDailyFallback(unittest.TestCase):
    def test_daily_sl(self):
        d = _day("20251202", 10000, None, o=10000.0, low=9000.0, c=9900.0)
        r = tp_sl_exit([d], 0, 10000, tp_px=11000, sl_px=9500)
        self.assertEqual(r["reason"], "daily_sl")
        self.assertAlmostEqual(r["exit_px"], 9500.0)

    def test_daily_close(self):
        d = _day("20251202", 10000, None, o=10000.0, low=9600.0, c=9900.0)
        r = tp_sl_exit([d], 0, 10000, tp_px=11000, sl_px=9500)
        self.assertEqual(r["reason"], "daily_close")
        self.assertAlmostEqual(r["exit_px"], 9900.0)

    def test_no_data_short(self):
        d = _day("20251202", 10000, None, o=10000.0, low=9600.0, c=9900.0)
        r = tp_sl_exit([d], 0, 10000, hold_days=1)
        self.assertEqual(r["reason"], "no_data")
        self.assertTrue(np.isnan(r["ret"]))


if __name__ == "__main__":
    unittest.main()
