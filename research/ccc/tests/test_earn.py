"""합성 데이터로 earn 검증 (R1~R4)."""

import unittest

import numpy as np
import pandas as pd

from research.ccc.earn import build_reg, earnings_panel, fama_macbeth
from research.ccc.panel import quarter_flow


def _df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["cik", "tag", "start", "end", "filed", "val"])
    df["start"] = pd.to_datetime(df["start"].replace("", None))
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df["val"] = df["val"].astype(float)
    return df


class TestEarn(unittest.TestCase):
    def test_r1_q4_keeps_negative_loss(self) -> None:
        f = _df([
            ("0000000001", "NetIncomeLoss", "2020-01-01", "2020-12-31", "2021-02-01", 100.0),
            ("0000000001", "NetIncomeLoss", "2020-01-01", "2020-09-30", "2020-10-30", 150.0),
        ])
        keep = quarter_flow(f, ("NetIncomeLoss",), allow_negative=True)
        self.assertEqual(len(keep), 1)
        self.assertAlmostEqual(keep.iloc[0]["val"], -50.0)
        drop = quarter_flow(f, ("NetIncomeLoss",))
        self.assertTrue(drop.empty)

    def test_r2_denominator_is_prior_assets(self) -> None:
        f = _df([
            ("0000000001", "NetIncomeLoss", "2020-01-01", "2020-03-31", "2020-05-01", 100.0),
            ("0000000001", "NetIncomeLoss", "2020-04-01", "2020-06-30", "2020-08-01", 200.0),
            ("0000000001", "Assets", "", "2019-12-31", "2020-02-01", 1000.0),
            ("0000000001", "Assets", "", "2020-03-31", "2020-05-01", 1100.0),
            ("0000000001", "Assets", "", "2020-06-30", "2020-08-01", 1200.0),
        ])
        ep = earnings_panel(f)
        r1 = ep[ep["qend"] == pd.Timestamp("2020-03-31")].iloc[0]
        self.assertAlmostEqual(r1["e"], 100.0 / 1000.0)
        self.assertAlmostEqual(r1["ln_at"], float(np.log(1100.0)))
        r2 = ep[ep["qend"] == pd.Timestamp("2020-06-30")].iloc[0]
        self.assertAlmostEqual(r2["e"], 200.0 / 1100.0)

    def test_r3_matches_only_90d_ahead(self) -> None:
        events = pd.DataFrame({"cik": ["0000000001"],
                               "qend": pd.to_datetime(["2020-03-31"]),
                               "dec": [1]})
        ep = pd.DataFrame({
            "cik": ["0000000001"] * 3,
            "qend": pd.to_datetime(["2020-03-31", "2020-06-30", "2020-09-30"]),
            "e": [0.05, 0.06, 0.07],
            "ln_at": [8.0, 8.1, 8.2],
            "neg": [False, False, False],
        })
        reg = build_reg(events, ep)
        self.assertEqual(len(reg), 1)
        self.assertEqual(reg.iloc[0]["q1"], pd.Timestamp("2020-06-30"))
        self.assertAlmostEqual(reg.iloc[0]["y"], 0.06)
        self.assertAlmostEqual(reg.iloc[0]["e_lag"], 0.05)

    def test_r4_quarterly_ols_low_coef(self) -> None:
        parts = []
        for cq in (20201, 20202):
            n = 40
            idx = np.arange(n)
            blk = idx // 10
            r = idx % 10
            low = blk == 0
            high = blk == 1
            parts.append(pd.DataFrame({
                "cq": cq,
                "y": 0.01 + 0.02 * low.astype(float),
                "low": low,
                "high": high,
                "ln_at_lag": 8.0 + 0.1 * r,
                "e_lag": 0.01 * r + 0.001 * r * r,
                "neg_lag": (r % 2 == 0),
            }))
        reg = pd.concat(parts, ignore_index=True)
        out = fama_macbeth(reg)
        row = out[out["term"] == "low"].iloc[0]
        self.assertAlmostEqual(float(row["coef"]), 0.02, places=6)
        self.assertEqual(int(row["nq"]), 2)


if __name__ == "__main__":
    unittest.main()
