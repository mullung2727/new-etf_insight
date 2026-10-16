"""합성 데이터로 delta 검증 (D1~D4)."""

import unittest

import pandas as pd

from research.ccc.delta import add_delta


def _row(ym, cik, qend, ccc, ind=1):
    return {"ym": ym, "cik": cik, "ticker": cik, "qend": pd.Timestamp(qend),
            "ccc": float(ccc), "ccc_adj": 0.0, "ind": ind, "ret": 0.01,
            "me": 1000.0, "market": "NYSE", "microcap": False}


class TestDelta(unittest.TestCase):
    def test_d1_prior_year_match(self) -> None:
        cs = pd.DataFrame([_row(201907, "A", "2019-03-31", 100),
                           _row(201910, "A", "2019-06-30", 80),
                           _row(202007, "A", "2020-03-31", 60)])
        out = add_delta(cs)
        self.assertEqual(set(out["ym"]), {202007})
        self.assertAlmostEqual(out.iloc[0]["ccc_lag"], 100.0)
        self.assertAlmostEqual(out.iloc[0]["dccc"], -40.0)

    def test_d2_no_prior_excluded(self) -> None:
        cs = pd.DataFrame([_row(201907, "B", "2019-03-31", 100),
                           _row(201910, "B", "2019-06-30", 80)])
        self.assertTrue(add_delta(cs).empty)

    def test_d3_first_appearance_wins(self) -> None:
        cs = pd.DataFrame([_row(201907, "C", "2019-03-31", 100),
                           _row(201908, "C", "2019-03-31", 120),
                           _row(202007, "C", "2020-03-31", 60)])
        out = add_delta(cs)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out.iloc[0]["ccc_lag"], 100.0)
        self.assertAlmostEqual(out.iloc[0]["dccc"], -40.0)

    def test_d4_ind_median_adj(self) -> None:
        rows = []
        for cik, cur, ind in (("A", 90, 1), ("B", 100, 1), ("C", 120, 1),
                              ("D", 150, 2), ("P1", 90, 3), ("P2", 150, 3)):
            rows.append(_row(201907, cik, "2019-03-31", 100, ind))
            rows.append(_row(202007, cik, "2020-03-31", cur, ind))
        out = add_delta(pd.DataFrame(rows))
        self.assertEqual(set(out["ym"]), {202007})
        o = out.set_index("cik")[["dccc", "dccc_adj"]].to_dict("index")
        for cik, d, a in (("A", -10.0, -10.0), ("B", 0.0, 0.0), ("C", 20.0, 20.0),
                          ("D", 50.0, 0.0), ("P1", -10.0, -30.0), ("P2", 50.0, 30.0)):
            self.assertAlmostEqual(o[cik]["dccc"], d, msg=cik)
            self.assertAlmostEqual(o[cik]["dccc_adj"], a, msg=cik)


if __name__ == "__main__":
    unittest.main()
