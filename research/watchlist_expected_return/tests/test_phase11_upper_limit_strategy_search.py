from __future__ import annotations

import unittest

from research.watchlist_expected_return.phase11_upper_limit_strategy_search import (
    candidate_strategies,
    infer_upper_limit,
    is_upper_limit_entry,
    select_final_candidates,
    summarize_excess_returns,
)


class UpperLimitGuardTest(unittest.TestCase):
    def test_infers_upper_limit_with_final_price_band_tick(self) -> None:
        self.assertEqual(infer_upper_limit(1_827), 2_375)
        self.assertEqual(infer_upper_limit(49_800), 64_700)

    def test_excludes_entry_at_or_above_upper_limit(self) -> None:
        self.assertTrue(is_upper_limit_entry(2_375, 1_827))
        self.assertFalse(is_upper_limit_entry(2_370, 1_827))


class StrategyMatrixTest(unittest.TestCase):
    def test_attempts_at_least_twenty_strategies(self) -> None:
        strategies = candidate_strategies()
        self.assertGreaterEqual(len(strategies), 20)
        self.assertEqual(len({item["id"] for item in strategies}), len(strategies))

    def test_final_selection_keeps_the_three_best_validated_results(self) -> None:
        candidates = [
            {
                "id": f"{entry}_{index}",
                "entry_rule": entry,
                "validated": True,
                "selection_score": score,
            }
            for index, (entry, score) in enumerate(
                [("a", 3.0), ("a", 2.9), ("b", 2.8), ("c", 2.7), ("d", 2.6)]
            )
        ]
        selected = select_final_candidates(candidates, limit=3)
        self.assertEqual([item["id"] for item in selected], ["a_0", "a_1", "b_2"])

    def test_relative_summary_uses_stock_return_minus_index_return(self) -> None:
        rows = [
            {"stock_return": 0.05, "index_return": 0.02},
            {"stock_return": -0.01, "index_return": 0.01},
        ]
        result = summarize_excess_returns(rows)
        self.assertEqual(result["count"], 2)
        self.assertAlmostEqual(result["mean_excess"], 0.005)
        self.assertEqual(result["outperform_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
