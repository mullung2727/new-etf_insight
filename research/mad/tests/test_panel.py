"""MAD panel 합성 테스트 (DB 접근 없음). T1~T5."""
import unittest

import numpy as np
import pandas as pd

from research.mad.panel import features, monthly_panel


def _daily(n=300, r=0.001, gap_after=None, gap_days=0,
           tickers=("AAA", "BBB")):
    rows = []
    dates = pd.date_range("2020-01-01", periods=n, freq="D").strftime("%Y%m%d")
    for t in tickers:
        ms = np.arange(n)
        if gap_after is not None:
            ms[gap_after:] += gap_days
        rr = np.full(n, r)
        rr[0] = np.nan
        for i in range(n):
            rows.append({"ticker": t, "date": dates[i], "ms": int(ms[i]),
                         "r": float(rr[i]), "raw_close": 10000.0, "tv": 1e10,
                         "me": 1e12})
    return pd.DataFrame(rows)


def _frow(t, d, r):
    return {"ticker": t, "date": d, "ms": 0, "r": r, "raw_close": 100.0,
            "tv": 1e9, "me": 1e11, "P": 1.0, "tv20": 1e9, "age": 300,
            "mad": 1.0, "align": 1.0, "above200": 1.0, "mds": 1.0,
            "hi52": 0.9}


class TestFeatures(unittest.TestCase):
    def test_t1_ma_values(self):
        f = features(_daily())
        a = f[f["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
        P = 1.001 ** np.arange(300)
        self.assertTrue(np.isnan(a.loc[18, "MA20"]))
        self.assertAlmostEqual(a.loc[19, "MA20"], float(P[:20].mean()),
                               places=9)
        self.assertTrue(a.loc[:198, "MA200"].isna().all())
        self.assertAlmostEqual(a.loc[199, "MA200"], float(P[:200].mean()),
                               places=9)
        self.assertTrue(np.isnan(a.loc[18, "tv20"]))
        self.assertAlmostEqual(a.loc[19, "tv20"], 1e10)
        self.assertEqual(a.loc[0, "age"], 0)
        self.assertEqual(a.loc[299, "age"], 299)
        self.assertTrue(np.isnan(a.loc[199, "mad"]))
        self.assertTrue(np.isfinite(a.loc[200, "mad"]))
        self.assertAlmostEqual(a.loc[252, "hi52"], 1.0, places=9)

    def test_t2_prevday_shift(self):
        base = _daily()
        new = base.copy()
        last = new[new["ticker"] == "AAA"].index[-1]
        new.loc[last, "r"] = 0.29
        mb = features(base)
        mn = features(new)
        b = mb[mb["ticker"] == "AAA"].sort_values("date").reset_index(
            drop=True)
        n = mn[mn["ticker"] == "AAA"].sort_values("date").reset_index(
            drop=True)
        self.assertAlmostEqual(b.loc[299, "mad"], n.loc[299, "mad"])
        self.assertNotAlmostEqual(b.loc[299, "MA200"], n.loc[299, "MA200"])

    def test_t3_coverage_gap(self):
        g = features(_daily(gap_after=100, gap_days=30))
        a = g[g["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
        c = features(_daily())
        k = c[c["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
        self.assertTrue(np.isnan(a.loc[250, "MA200"]))
        self.assertTrue(np.isfinite(k.loc[250, "MA200"]))
        self.assertTrue(np.isnan(a.loc[250, "mad"]))
        self.assertTrue(np.isfinite(k.loc[250, "mad"]))
        self.assertTrue(np.isfinite(a.loc[299, "MA200"]))

    def test_t5_ticker_boundary(self):
        df = _daily().iloc[::-1].reset_index(drop=True)
        f = features(df)
        self.assertListEqual(f["ticker"].tolist(), df["ticker"].tolist())
        b = f[f["ticker"] == "BBB"].sort_values("date").reset_index(drop=True)
        self.assertTrue(b.loc[:198, "MA200"].isna().all())
        P = 1.001 ** np.arange(300)
        self.assertAlmostEqual(b.loc[199, "MA200"], float(P[:200].mean()),
                               places=9)
        self.assertAlmostEqual(b.loc[0, "P"], 1.0)
        a = f[f["ticker"] == "AAA"].sort_values("date").reset_index(drop=True)
        self.assertAlmostEqual(a.loc[0, "P"], 1.0)


class TestMonthly(unittest.TestCase):
    def test_t4_monthly_returns(self):
        fdates = ["20200131", "20200229", "20200331", "20200430", "20200531",
                  "20200630", "20200731", "20200831", "20200930", "20201031",
                  "20201130", "20201231", "20210131", "20210228", "20210331"]
        rA = [np.nan, 0.01, 0.02, np.nan, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09,
              0.10, 0.11, 0.12, 0.13, 0.14]
        rows = [_frow("A", d, r) for d, r in zip(fdates, rA)]
        rows.append(_frow("A", "20200615", 0.05))
        rows += [_frow("B", d, r) for d, r in zip(fdates[:8], rA[:8])]
        p = monthly_panel(pd.DataFrame(rows), fdates, H=6)
        pa = p[p["ticker"] == "A"].sort_values("ym").reset_index(drop=True)
        pb = p[p["ticker"] == "B"].sort_values("ym").reset_index(drop=True)
        self.assertEqual(pa.loc[0, "ym"], 202001)
        self.assertAlmostEqual(pa.loc[0, "ret_1"], 0.01)
        self.assertAlmostEqual(pa.loc[0, "ret_2"], 0.02)
        self.assertAlmostEqual(pa.loc[2, "ret_1"], 0.0)
        self.assertAlmostEqual(pa.loc[4, "ret_1"], 1.05 * 1.05 - 1)
        self.assertAlmostEqual(pa.loc[5, "ret_1"], 0.06)
        self.assertAlmostEqual(pa.loc[13, "ret_1"], 0.14)
        self.assertTrue(np.isnan(pa.loc[14, "ret_1"]))
        self.assertTrue(np.isnan(pa.loc[13, "ret_2"]))
        exp_mom12 = (1.01 * 1.02 * 1.0 * 1.04 * (1.05 * 1.05)
                     * 1.06 * 1.07 * 1.08 * 1.09 * 1.10 * 1.11) - 1
        self.assertAlmostEqual(pa.loc[12, "mom"], exp_mom12)
        self.assertTrue(np.isnan(pa.loc[11, "mom"]))
        self.assertTrue(np.isnan(pa.loc[0, "ret_1m"]))
        self.assertAlmostEqual(pa.loc[5, "ret_1m"], 1.05 * 1.05 - 1)
        self.assertAlmostEqual(pa.loc[6, "ret_1m"], 0.06)
        self.assertEqual(len(pb), 8)
        self.assertAlmostEqual(pb.loc[7, "ret_1"], 0.0)
        self.assertAlmostEqual(pb.loc[7, "ret_2"], 0.0)


if __name__ == "__main__":
    unittest.main()
