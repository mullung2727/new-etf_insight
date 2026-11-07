"""cv.folds 테스트 (합성 units만, 실파일 읽지 않음)."""

from __future__ import annotations

import unittest

from research.rl_signal.cv import folds

DAYS = ["2026110%d" % i for i in range(1, 5)]
CODES = ["A", "B", "C", "D", "E"]
UNITS = [(d, c) for d in DAYS for c in CODES]


class TestFolds(unittest.TestCase):
    def test_k_folds(self):
        for by, k in (("code", 5), ("day", 4), ("code", 3), ("day", 2)):
            self.assertEqual(len(folds(UNITS, by, k, 0)), k)

    def test_no_overlap_code(self):
        for tr, te in folds(UNITS, "code", 5, 0):
            tc = {c for _, c in tr}
            ec = {c for _, c in te}
            self.assertFalse(tc & ec)
            self.assertEqual(tc | ec, set(CODES))

    def test_no_overlap_day(self):
        for tr, te in folds(UNITS, "day", 4, 1):
            td = {d for d, _ in tr}
            ed = {d for d, _ in te}
            self.assertFalse(td & ed)
            self.assertEqual(td | ed, set(DAYS))

    def test_each_once(self):
        for by, k in (("code", 5), ("day", 4), ("code", 3), ("day", 3)):
            all_test = [u for _, te in folds(UNITS, by, k, 7) for u in te]
            self.assertEqual(sorted(all_test), sorted(UNITS))

    def test_bad_by(self):
        with self.assertRaises(ValueError):
            folds(UNITS, "x", 2, 0)


if __name__ == "__main__":
    unittest.main()
