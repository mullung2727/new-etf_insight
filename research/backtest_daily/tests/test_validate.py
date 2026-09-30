"""validate 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import inspect
import unittest

import pandas as pd

from research.backtest_daily.validate import wf_train


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


if __name__ == "__main__":
    unittest.main()
