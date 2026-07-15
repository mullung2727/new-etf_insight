from __future__ import annotations

import unittest

from research.watchlist_expected_return.phase7_pullback_strategy import find_entry, simulate_exit


def sample_row() -> dict:
    return {
        "date": "20260102", "ticker": "000001", "base_close": 100,
        "history": [
            {"date": f"2025122{i}", "open": 100, "high": 102, "low": 98, "close": 100}
            for i in range(5)
        ],
        "future": [
            {"date": "20260105", "open": 99, "high": 100, "low": 96, "close": 98},
            {"date": "20260106", "open": 98, "high": 103, "low": 97, "close": 102},
            {"date": "20260107", "open": 102, "high": 104, "low": 101, "close": 103},
            {"date": "20260108", "open": 103, "high": 104, "low": 102, "close": 103},
            {"date": "20260109", "open": 103, "high": 104, "low": 102, "close": 103},
            {"date": "20260112", "open": 103, "high": 104, "low": 102, "close": 103},
        ],
    }


class Phase7PullbackStrategyTest(unittest.TestCase):
    def test_limit_entry_uses_limit_price(self) -> None:
        entry = find_entry(sample_row(), "limit_down_3pct")
        self.assertEqual(entry["entry_price"], 97)
        self.assertEqual(entry["wait_days"], 1)

    def test_gap_below_limit_gets_open_price(self) -> None:
        row = sample_row()
        row["future"][0]["open"] = 94
        row["future"][0]["low"] = 93
        entry = find_entry(row, "limit_down_5pct")
        self.assertEqual(entry["entry_price"], 94)

    def test_untriggered_rule_means_no_buy(self) -> None:
        row = sample_row()
        for day in row["future"]:
            day.update(open=101, high=102, low=99, close=100)
        self.assertIsNone(find_entry(row, "limit_down_3pct"))

    def test_holding_starts_after_entry_day(self) -> None:
        row = sample_row()
        entry = find_entry(row, "limit_down_3pct")
        outcome = simulate_exit(row, entry, {"kind": "fixed_close", "max_hold_days": 1})
        self.assertAlmostEqual(outcome["gross_return"], 102 / 97 - 1)
        self.assertEqual(outcome["holding_days"], 1)

    def test_suspended_day_cannot_be_used_as_exit(self) -> None:
        row = sample_row()
        entry = find_entry(row, "limit_down_3pct")
        row["future"][1].update(open=0, high=0, low=0, volume=0)
        self.assertIsNone(simulate_exit(row, entry, {"kind": "fixed_close", "max_hold_days": 1}))


if __name__ == "__main__":
    unittest.main()
