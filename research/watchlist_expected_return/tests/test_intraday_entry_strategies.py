from __future__ import annotations

import unittest

from research.watchlist_expected_return.five_minute_high_breakout import find_entry as find_high_break
from research.watchlist_expected_return.phase10_intraday_entry_comparison import (
    close_confirm,
    exit_candidates,
    find_production_signal,
)
from research.watchlist_expected_return.prior_low_reclaim import find_entry as find_reclaim
from research.watchlist_expected_return.vwap_reclaim import find_entry as find_vwap


def bar(index: int, *, open_: float = 100, high: float = 101, low: float = 99, close: float = 100, volume: int = 10):
    return {"date": "20260701", "time": f"09{index:02d}00", "timestamp": f"2026070109{index:02d}00",
            "open": open_, "high": high, "low": low, "close": close, "volume": volume}


class IntradayEntryStrategiesTest(unittest.TestCase):
    def test_close_confirm_uses_exact_1519_bar(self):
        bars = [
            {**bar(0, low=98, close=99), "time": "090000", "timestamp": "20260701090000"},
            {**bar(1, low=99, close=103), "time": "151900", "timestamp": "20260701151900"},
            {**bar(2, low=99, close=110), "time": "153000", "timestamp": "20260701153000"},
        ]
        result = close_confirm(bars, 100)
        self.assertEqual(103, result["entry_price"])
        self.assertEqual("20260701151900", result["entry_timestamp"])

    def test_close_confirm_rejects_bullish_move_after_1519(self):
        bars = [
            {**bar(0, low=98, close=99), "time": "090000", "timestamp": "20260701090000"},
            {**bar(1, low=99, close=99), "time": "151900", "timestamp": "20260701151900"},
            {**bar(2, low=99, close=110), "time": "153000", "timestamp": "20260701153000"},
        ]
        self.assertIsNone(close_confirm(bars, 100))

    def test_production_signal_continues_after_first_bearish_lower_low(self):
        bars = [
            {**bar(0, open_=100, low=98, close=99), "date": "20260701", "time": "090000", "timestamp": "20260701090000"},
            {**bar(1, open_=100, low=98, close=99), "date": "20260701", "time": "151900", "timestamp": "20260701151900"},
            {**bar(0, open_=99, low=97, close=99), "date": "20260702", "time": "090000", "timestamp": "20260702090000"},
            {**bar(1, open_=99, low=97, close=101), "date": "20260702", "time": "151900", "timestamp": "20260702151900"},
        ]
        result = find_production_signal(bars, ["20260701", "20260702"], 100)
        self.assertEqual((1, "20260702", 98), result[:3])
        self.assertEqual(101, result[3]["entry_price"])

    def test_exit_grid_excludes_small_stops_and_requires_larger_take_profit(self):
        candidates = exit_candidates()
        self.assertEqual(72, len(candidates))
        self.assertEqual({0.03, 0.04, 0.05}, {item["sl"] for item in candidates})
        self.assertEqual({1, 2, 3, 5}, {item["days"] for item in candidates})
        self.assertTrue(all(item["tp"] > item["sl"] for item in candidates))

    def test_prior_low_reclaim_accepts_breach_and_reclaim_in_same_bar(self):
        result = find_reclaim([bar(0, low=98, close=101)], 100)
        self.assertEqual(result["entry_price"], 101)

    def test_five_minute_high_break_waits_for_previous_five_high(self):
        bars = [bar(i, low=98 if i == 1 else 99, high=101) for i in range(6)]
        bars.append(bar(6, high=103, close=102))
        self.assertEqual(find_high_break(bars, 100)["entry_price"], 102)

    def test_vwap_reclaim_requires_cross_from_below(self):
        bars = [bar(0, high=102, low=98, close=99, volume=10),
                bar(1, high=103, low=99, close=102, volume=10)]
        self.assertEqual(find_vwap(bars, 100)["entry_price"], 102)


if __name__ == "__main__":
    unittest.main()
