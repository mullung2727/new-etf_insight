"""validate 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import inspect
import unittest

import pandas as pd

from research.backtest_minute.validate import walk_forward_monthly, wf_monthly


class TestWfMonthly(unittest.TestCase):
    def test_excludes_exit_after_cut(self):
        tr = pd.DataFrame([
            {"cand": "A", "entry_date": "20231201", "exit_date": "20231230"},
            {"cand": "A", "entry_date": "20231201", "exit_date": "20240115"},
            {"cand": "A", "entry_date": "20240105", "exit_date": "20240201"},
            {"cand": "A", "entry_date": "20231215", "exit_date": "20231231"},
        ])
        got = wf_monthly(tr, "202401")
        self.assertEqual(sorted(got["exit_date"].tolist()), ["20231230", "20231231"])

    def test_signature_locked(self):
        self.assertEqual(tuple(inspect.signature(wf_monthly).parameters),
                         ("trades", "test_month"))


class TestWalkForwardMonthly(unittest.TestCase):
    def test_generator_same_as_list(self):
        rows = []
        for mon in ("202401", "202402", "202403"):
            for cand, exc in (("A", 0.05), ("B", -0.05)):
                for k in range(3):
                    rows.append({"cand": cand, "exc": exc, "m": f"{mon}{k}",
                                 "entry_date": f"{mon}15", "exit_date": f"{mon}20"})
        tr = pd.DataFrame(rows)
        d1, o1 = walk_forward_monthly(tr, ["202402", "202403"], ["A", "B"], "m", min_n=2)
        d2, o2 = walk_forward_monthly(tr, ["202402", "202403"],
                                      (c for c in ["A", "B"]), "m", min_n=2)
        self.assertEqual(d1, d2)
        pd.testing.assert_frame_equal(o1, o2)
        self.assertEqual(d1["202402"]["selected"], "A")

    def test_duplicate_months_raises(self):
        tr = pd.DataFrame([
            {"cand": "A", "exc": 0.05, "m": "x",
             "entry_date": "20260215", "exit_date": "20260220"},
        ])
        with self.assertRaisesRegex(ValueError, "중복 월"):
            walk_forward_monthly(tr, ["202602", "202602"], ["A"], "m")


if __name__ == "__main__":
    unittest.main()
