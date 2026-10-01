"""validate 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import inspect
import unittest

import pandas as pd

from research.backtest_daily.validate import walk_forward, wf_train


class TestWfTrain(unittest.TestCase):
    def test_excludes_exit_after_cut(self):
        tr = pd.DataFrame([
            {"cand": "A_N20_H20", "entry_date": "20221201", "exit_date": "20221230"},
            {"cand": "A_N20_H20", "entry_date": "20221201", "exit_date": "20230115"},
            {"cand": "A_N20_H20", "entry_date": "20230105", "exit_date": "20230201"},
            {"cand": "A_N20_H20", "entry_date": "20221215", "exit_date": "20221231"},
        ])
        got = wf_train(tr, 2023)
        self.assertEqual(sorted(got["exit_date"].tolist()), ["20221230", "20221231"])
        self.assertTrue((got["exit_date"] < "20230101").all())

    def test_signature_locked(self):
        self.assertEqual(tuple(inspect.signature(wf_train).parameters), ("trades", "year"))


class TestWalkForwardCands(unittest.TestCase):
    def test_generator_same_as_list(self):
        rows = []
        for year in (2022, 2023, 2024):
            for cand, exc in (("A", 0.05), ("B", -0.05)):
                for k in range(3):
                    rows.append({"cand": cand, "exc": exc, "m": f"{year}{k:02d}",
                                 "entry_date": f"{year}0601",
                                 "exit_date": f"{year}0701"})
        tr = pd.DataFrame(rows)
        d1, o1 = walk_forward(tr, [2023, 2024], ["A", "B"], "m", min_n=2)
        d2, o2 = walk_forward(tr, [2023, 2024], (c for c in ["A", "B"]), "m",
                              min_n=2)
        self.assertEqual(d1, d2)
        pd.testing.assert_frame_equal(o1, o2)
        self.assertEqual(d1["2023"]["selected"], "A")
        self.assertEqual(d1["2024"]["selected"], "A")


if __name__ == "__main__":
    unittest.main()
