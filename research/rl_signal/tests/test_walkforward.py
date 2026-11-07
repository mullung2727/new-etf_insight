"""walkforward 테스트 (합성 날짜만, 실 parquet 읽지 않음)."""

from __future__ import annotations

import unittest

import numpy as np

from research.rl_signal.bars import FEATURES
from research.rl_signal.dataset import samples
from research.rl_signal.evaluate import summarize
from research.rl_signal.oracle import BUY, HOLD, SELL
from research.rl_signal.walkforward import _nanmean, splits

DAYS = ["2026110%d" % i for i in range(1, 8)]


class TestSplits(unittest.TestCase):
    def test_basic(self):
        sp = splits(DAYS, 5)
        self.assertEqual(len(sp), 2)
        self.assertEqual(sp[0], (DAYS[:5], DAYS[5]))
        self.assertEqual(sp[1], (DAYS[:6], DAYS[6]))

    def test_no_leak(self):
        for train, test in splits(DAYS, 5):
            self.assertNotIn(test, train)

    def test_min_train_too_big(self):
        self.assertEqual(splits(DAYS, 7), [])
        self.assertEqual(splits(DAYS, 99), [])


class TestNanmean(unittest.TestCase):
    def test_mean(self):
        self.assertAlmostEqual(_nanmean([0.1, 0.2, 0.3]), 0.2)

    def test_skips_nan(self):
        self.assertAlmostEqual(_nanmean([0.1, float("nan"), 0.3]), 0.2)

    def test_all_nan(self):
        self.assertTrue(np.isnan(_nanmean([float("nan")])))
        self.assertTrue(np.isnan(_nanmean([])))


def _entry(model, bh, oracle, pr, pt, trades=1):
    return {"model": model, "model_slip1": model - 0.001, "trades": trades,
            "log_total": model, "log_total_slip1": model - 0.001,
            "bh": bh, "oracle": oracle,
            "pr": np.array(pr, dtype=float), "pt": np.array(pt, dtype=float),
            "ps": np.array(pr, dtype=float)}


class TestSeenUnseenContract(unittest.TestCase):
    """main()이 의존하는 식의 계약 고정 (main 자체는 parquet 없이 실행 불가).

    seen 합집합 분리 + summarize(per, seen/unseen) + json seen/unseen 키(n·model·bh·diag_capture).
    """

    def test_split_and_json_keys(self):
        codes = ["A", "B", "C"]
        seen = {"A", "C"}
        per = {
            "A": _entry(0.02, 0.01, 0.05, [0.0, 0.02], [0.01, 0.01]),
            "B": _entry(0.04, -0.01, 0.07, [0.02, 0.02], [0.0, 0.06]),
            "C": _entry(0.00, 0.02, 0.03, [0.01, 0.01], [0.02, 0.02]),
        }
        s_seen = summarize(per, [c for c in codes if c in seen])
        s_unseen = summarize(per, [c for c in codes if c not in seen])
        self.assertEqual(s_seen["n"], 2)
        self.assertAlmostEqual(s_seen["model"], 0.01)
        self.assertEqual(s_unseen["n"], 1)
        self.assertAlmostEqual(s_unseen["model"], 0.04)
        for s in (s_seen, s_unseen):
            for k in ("n", "model", "bh", "diag_capture"):
                self.assertIn(k, s)

    def test_empty_side(self):
        per = {"A": _entry(0.02, 0.01, 0.05, [0.0], [0.01])}
        s = summarize(per, [])
        self.assertEqual(s["n"], 0)
        self.assertTrue(np.isnan(s["model"]))


class TestShuffleSeeds(unittest.TestCase):
    """main()의 일자별 시드 계약: 같은 day 섞기 표본 X = 원 표본 X, 라벨만 다름."""

    def test_same_day_same_x(self):
        n = 8
        q = np.full((n, 2, 6, 3), -np.inf)
        for t in range(n):
            q[t, :, :, HOLD] = 0.0
            q[t, :, :, BUY] = float(t)
            q[t, :, :, SELL] = float(t)
        cells = {"AAA": {"feat": np.random.default_rng(0).normal(size=(n, len(FEATURES))).astype(np.float32),
                         "q": q}}
        seed, day = 0, "20261110"
        X0, y0, _, _ = samples(cells, np.random.default_rng([seed, int(day)]))
        X1, y1, _, _ = samples(cells, np.random.default_rng([seed, int(day)]),
                               np.random.default_rng([seed, int(day), 1000]))
        np.testing.assert_array_equal(X1, X0)
        self.assertFalse((y1 == y0).all())


if __name__ == "__main__":
    unittest.main()
