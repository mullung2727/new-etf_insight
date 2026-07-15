from __future__ import annotations

import unittest

from research.watchlist_expected_return.phase9_minute_strategy_design import eligible, passes_filter


class Phase9DesignTest(unittest.TestCase):
    def test_quality_combo_requires_all_three_conditions(self):
        features = {"entry_time": "101500", "rebound_from_low": 0.021, "lower_low_depth": -0.02, "volume_ratio_5": 1.0}
        self.assertTrue(passes_filter(features, "quality_combo"))
        self.assertFalse(passes_filter({**features, "entry_time": "095900"}, "quality_combo"))

    def test_eligibility_uses_robust_early_metrics(self):
        metrics = {"count": 10, "mean": 0.01, "median": 0.005, "p10": -0.05}
        self.assertTrue(eligible(metrics))
        self.assertFalse(eligible({**metrics, "p10": -0.051}))


if __name__ == "__main__":
    unittest.main()
