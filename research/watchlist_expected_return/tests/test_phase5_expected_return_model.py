from __future__ import annotations

import unittest

from research.watchlist_expected_return.phase5_expected_return_model import (
    _percentile_scores,
    fit_ridge,
    predict,
    walk_forward_predictions,
)


def model_row(index: int, date: str) -> dict:
    value = float(index + 1)
    return {
        "date": date,
        "ticker": f"{index:06d}",
        "entry_volume": value * 100,
        "entry_close": value * 10,
        "market_cap": value * 1000,
        "trading_value": value * 500,
        "ratio": value,
        "intraday_rank": 100 - index,
        "target_net_return": value / 100,
    }


class Phase5ExpectedReturnModelTest(unittest.TestCase):
    def test_ridge_prediction_increases_with_synthetic_signal(self) -> None:
        rows = [model_row(index, "20260101") for index in range(20)]
        model = fit_ridge(rows)
        self.assertGreater(predict(model, rows[-1]), predict(model, rows[0]))

    def test_walk_forward_uses_only_prior_dates(self) -> None:
        rows = []
        for date_index in range(6):
            date = f"202601{date_index + 1:02d}"
            rows.extend(model_row(date_index * 10 + index, date) for index in range(10))
        predictions = walk_forward_predictions(rows, min_train_rows=30)
        self.assertTrue(predictions)
        self.assertTrue(all(row["training_count"] >= 30 for row in predictions))
        self.assertTrue(all(row["date"] >= "20260104" for row in predictions))

    def test_percentile_score_is_bounded(self) -> None:
        scores = _percentile_scores([3.0, 1.0, 2.0])
        self.assertEqual(scores, [100, 0, 50])
        self.assertTrue(all(0 <= score <= 100 for score in scores))


if __name__ == "__main__":
    unittest.main()
