from __future__ import annotations

import unittest

from research.watchlist_expected_return.phase3_rising_features import analyze_features


class Phase3RisingFeaturesTest(unittest.TestCase):
    def test_repeated_feature_direction_becomes_candidate(self) -> None:
        rows = []
        for index in range(20):
            rising = index % 2 == 0
            rows.append({
                "date": "20260102" if index < 10 else "20260202",
                "gap_return_d1_open": 0.02 if rising else -0.01,
                "ratio": 10.0 if rising else 1.0,
                "trading_value": 1000 + index,
                "today_volume": 100 + index,
                "avg5_volume": 50,
                "entry_close": 100,
                "entry_volume": 100,
                "market_cap": 1000,
                "intraday_rank": 1 if rising else 20,
                "source_count": 3 if rising else 1,
                "has_board_evidence": True,
                "has_news_evidence": rising,
                "has_web_evidence": rising,
            })
        result = analyze_features(rows)
        self.assertIn("ratio", result["primary_candidates"])
        self.assertIn("intraday_rank", result["primary_candidates"])
        self.assertEqual(
            result["sensitivity"]["return_gt_0.000"]["features"]["ratio"]["winner_direction"],
            "higher",
        )

    def test_missing_feature_over_twenty_percent_is_not_candidate(self) -> None:
        rows = []
        for index in range(10):
            rows.append({
                "date": "20260102" if index < 5 else "20260202",
                "gap_return_d1_open": 0.01 if index % 2 == 0 else -0.01,
                **{feature: 1 for feature in (
                    "ratio", "trading_value", "today_volume", "avg5_volume", "entry_close",
                    "entry_volume", "market_cap", "intraday_rank", "source_count",
                )},
                "has_board_evidence": False,
                "has_news_evidence": False,
                "has_web_evidence": False,
            })
        for row in rows[:3]:
            row["ratio"] = None
        result = analyze_features(rows)
        self.assertFalse(result["sensitivity"]["return_gt_0.000"]["features"]["ratio"]["candidate"])


if __name__ == "__main__":
    unittest.main()
