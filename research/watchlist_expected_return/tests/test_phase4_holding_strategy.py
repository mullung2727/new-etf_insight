from __future__ import annotations

import unittest

from research.watchlist_expected_return.phase4_holding_strategy import (
    analyze_strategies,
    simulate_fixed_close,
    simulate_tp_sl,
    summarize_outcomes,
)


def sample_row(**prices):
    base = {
        "date": "20260102",
        "ticker": "000001",
        "entry_close": 100,
        "price_path": [
            {"horizon": 1, "date": "20260105", "open": 100, "high": 106, "low": 96, "close": 102},
            {"horizon": 2, "date": "20260106", "open": 102, "high": 104, "low": 99, "close": 103},
        ],
    }
    base.update(prices)
    return base


class Phase4HoldingStrategyTest(unittest.TestCase):
    def test_same_day_both_touch_uses_requested_policy(self) -> None:
        row = sample_row(price_path=[
            {"horizon": 1, "date": "20260105", "open": 100, "high": 106, "low": 96, "close": 102}
        ])
        conservative = simulate_tp_sl(row, 0.05, 0.03, 1, "sl_first")
        optimistic = simulate_tp_sl(row, 0.05, 0.03, 1, "tp_first")
        self.assertEqual(conservative["gross_return"], -0.03)
        self.assertEqual(optimistic["gross_return"], 0.05)

    def test_gap_exit_uses_open_price(self) -> None:
        row = sample_row(price_path=[
            {"horizon": 1, "date": "20260105", "open": 90, "high": 95, "low": 85, "close": 92}
        ])
        outcome = simulate_tp_sl(row, 0.05, 0.03, 1)
        self.assertAlmostEqual(outcome["gross_return"], -0.1)
        self.assertEqual(outcome["exit_reason"], "gap_sl")

    def test_no_touch_exits_at_selected_day_close(self) -> None:
        outcome = simulate_fixed_close(sample_row(), 2)
        self.assertAlmostEqual(outcome["gross_return"], 0.03)
        threshold = simulate_tp_sl(sample_row(), 0.10, 0.10, 2)
        self.assertAlmostEqual(threshold["gross_return"], 0.03)
        self.assertEqual(threshold["exit_reason"], "forced_close")

    def test_cost_is_subtracted_from_gross_return(self) -> None:
        metrics = summarize_outcomes([
            {"entry_date": "20260102", "gross_return": 0.02, "holding_days": 1},
            {"entry_date": "20260103", "gross_return": 0.00, "holding_days": 2},
        ], 0.01)
        self.assertAlmostEqual(metrics["mean"], 0.0)
        self.assertEqual(metrics["positive_rate"], 0.5)

    def test_negative_late_mean_cannot_validate_strategy(self) -> None:
        rows = []
        for index in range(40):
            rows.append(sample_row(
                date="20260102" if index < 20 else "20260202",
                ticker=f"{index:06d}",
                entry_volume=100,
                market_cap=1000,
                trading_value=1000,
            ))
        result = analyze_strategies(rows, cost_rate=0.10)
        self.assertFalse(result["validated_on_late"])


if __name__ == "__main__":
    unittest.main()
