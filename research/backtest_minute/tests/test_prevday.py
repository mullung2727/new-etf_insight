"""prevday 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import pandas as pd

from research.backtest_minute.prevday import attach_prev, leak_report


def _d():
    return pd.DataFrame([{"date": "20251202", "ticker": "A", "ms": 1, "pc": 100.0}])


def _feat_ok():
    return pd.DataFrame([{"ticker": "A", "f_ms": 0, "f_close": 100.0,
                          "f_date": "20251201", "x": 1.0}])


class TestAttachPrev(unittest.TestCase):
    def test_ok(self):
        m = attach_prev(_d(), _feat_ok())
        self.assertEqual(len(m), 1)
        self.assertEqual(m["x"].iloc[0], 1.0)

    def test_same_day_close_mismatch(self):
        f = _feat_ok()
        f["f_close"] = 110.0
        with self.assertRaises(AssertionError):
            attach_prev(_d(), f)

    def test_same_day_date(self):
        f = _feat_ok()
        f["f_date"] = "20251202"
        with self.assertRaises(AssertionError):
            attach_prev(_d(), f)


class TestLeakReport(unittest.TestCase):
    def test_bad_detected(self):
        n = 12
        pc = [100.0] * n
        dc = [100.0 + i for i in range(n)]
        d = pd.DataFrame({"pc": pc, "d_close": dc,
                          "bad": [c / 100 - 1 for c in dc],
                          "good": [0.0, 1.0] * (n // 2)})
        leak_report(d, ["good"])
        with self.assertRaises(AssertionError):
            leak_report(d, ["bad"])
        with self.assertRaises(AssertionError):
            leak_report(d, ["good", "bad"])


if __name__ == "__main__":
    unittest.main()
