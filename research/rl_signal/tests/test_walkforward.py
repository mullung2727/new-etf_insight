"""walkforward 테스트 (합성 날짜만, 실 parquet 읽지 않음)."""

from __future__ import annotations

import unittest

from research.rl_signal.walkforward import splits

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


if __name__ == "__main__":
    unittest.main()
