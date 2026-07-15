from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from research.watchlist_expected_return.minute_bar_cache import load_or_fetch_minutes
from research.watchlist_expected_return.phase8_minute_pullback_strategy import (
    find_lower_low_day,
    find_minute_entry,
    simulate_minute_exit,
)


def bar(timestamp: str, open_: int, high: int, low: int, close: int, volume: int = 100):
    return {"timestamp": timestamp, "date": timestamp[:8], "time": timestamp[8:],
            "open": open_, "high": high, "low": low, "close": close, "volume": volume}


class MinuteBarCacheTest(unittest.TestCase):
    def test_continuation_is_deduplicated_and_cached(self):
        calls = []
        pages = [
            {"bars": [{"cntr_tm": "20260702100000", "open_pric": "-100", "high_pric": "-102", "low_pric": "-99", "cur_prc": "-101", "trde_qty": "10"}], "cont_yn": "Y", "next_key": "next"},
            {"bars": [{"cntr_tm": "20260701090000", "open_pric": "100", "high_pric": "101", "low_pric": "98", "cur_prc": "99", "trde_qty": "20"}], "cont_yn": "N", "next_key": ""},
        ]

        def fetch(*args, **kwargs):
            calls.append((args, kwargs))
            return pages[len(calls) - 1]

        with tempfile.TemporaryDirectory() as directory:
            first = load_or_fetch_minutes("005930", "20260702", "20260701", cache_dir=Path(directory), fetch_page=fetch)
            second = load_or_fetch_minutes("005930", "20260702", "20260701", cache_dir=Path(directory), fetch_page=fetch)
        self.assertEqual(2, len(calls))
        self.assertTrue(first["complete"])
        self.assertEqual(first, second)
        self.assertEqual(["20260701090000", "20260702100000"], [item["timestamp"] for item in first["bars"]])
        self.assertEqual(100, first["bars"][1]["open"])


class MinuteStrategyTest(unittest.TestCase):
    def test_low_rebound_entry_occurs_after_lower_low(self):
        bars = [
            bar("20260701090000", 99, 101, 99, 100),
            bar("20260701090100", 100, 100, 98, 98),
            bar("20260701090200", 98, 100, 98, 100),
        ]
        entry = find_minute_entry(bars, "20260701", 99, "low_rebound_1pct")
        self.assertEqual("20260701090200", entry["entry_timestamp"])

    def test_lower_low_candidate_does_not_require_future_bullish_close(self):
        row = {"history": [{"low": 100}], "future": [{"low": 99, "open": 101, "close": 98}]}
        self.assertEqual((0, 100), find_lower_low_day(row))

    def test_tp_before_later_sl_uses_minute_order(self):
        bars = [
            bar("20260701090000", 100, 100, 99, 100),
            bar("20260701090100", 100, 103, 100, 102),
            bar("20260702090000", 97, 98, 96, 97),
        ]
        outcome = simulate_minute_exit(
            bars, {"entry_price": 100, "entry_timestamp": "20260701090000"},
            ["20260701", "20260702"], {"kind": "tp_sl", "tp": 0.03, "sl": 0.03, "days": 1},
        )
        self.assertEqual("tp", outcome["exit_reason"])

    def test_same_minute_both_is_stop_first(self):
        bars = [
            bar("20260701090000", 100, 100, 99, 100),
            bar("20260701090100", 100, 104, 96, 100),
            bar("20260702090000", 100, 101, 99, 100),
        ]
        outcome = simulate_minute_exit(
            bars, {"entry_price": 100, "entry_timestamp": "20260701090000"},
            ["20260701", "20260702"], {"kind": "tp_sl", "tp": 0.03, "sl": 0.03, "days": 1},
        )
        self.assertEqual("same_minute_both_sl", outcome["exit_reason"])
        self.assertEqual(-0.03, outcome["gross_return"])


if __name__ == "__main__":
    unittest.main()
