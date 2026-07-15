from __future__ import annotations

import unittest

from research.watchlist_expected_return.five_minute_high_breakout import find_entry as find_high_break
from research.watchlist_expected_return.prior_low_reclaim import find_entry as find_reclaim
from research.watchlist_expected_return.vwap_reclaim import find_entry as find_vwap


def bar(index: int, *, open_: float = 100, high: float = 101, low: float = 99, close: float = 100, volume: int = 10):
    return {"date": "20260701", "time": f"09{index:02d}00", "timestamp": f"2026070109{index:02d}00",
            "open": open_, "high": high, "low": low, "close": close, "volume": volume}


class IntradayEntryStrategiesTest(unittest.TestCase):
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
