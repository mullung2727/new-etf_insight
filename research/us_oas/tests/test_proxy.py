"""proxy 단위 테스트 — 합성 OAS·ETF만, DB·네트워크 없음 (unittest)."""
import math
import unittest

import numpy as np
import pandas as pd

from research.us_oas import proxy


def _days(start="2024-01-01", n=140):
    base = pd.Timestamp(start)
    return [(base + pd.Timedelta(days=k)).strftime("%Y%m%d") for k in range(n)]


def _etf(days):
    return pd.DataFrame(
        {
            "HYG": [0.002 * math.sin(k * 1.7) for k in range(len(days))],
            "IEI": [0.002 * math.cos(k * 0.9 + 0.4) for k in range(len(days))],
        },
        index=list(days),
    )


def _oas(days, vals):
    return pd.Series(list(vals), index=list(days))


class TestProxy(unittest.TestCase):
    def test_n1_nowcast_ignores_t_obs(self):
        days = _days(n=140)
        etf = _etf(days)
        oas = _oas(days, [3.0 + 0.001 * k for k in range(len(days))])
        dates = days[-10:]
        t = dates[2]
        base = proxy.nowcast(oas, etf, dates)
        self.assertTrue(bool(base.loc[t, "fitted"]))
        mod = oas.copy()
        mod.loc[t] = 99.0
        changed = proxy.nowcast(mod, etf, dates)
        self.assertEqual(changed.loc[t, "oas"], base.loc[t, "oas"])
        self.assertEqual(changed.loc[t, "d10_bp"], base.loc[t, "d10_bp"])

    def test_n2_train_only_before_t(self):
        days = _days(n=140)
        etf = _etf(days)
        oas = _oas(days, [3.0 + 0.001 * k for k in range(len(days))])
        dates = days[-10:]
        t = dates[2]
        base = proxy.nowcast(oas, etf, dates)
        mod = oas.copy()
        for d in days[days.index(t) + 1:]:
            mod.loc[d] = 99.0
        changed = proxy.nowcast(mod, etf, dates)
        pd.testing.assert_series_equal(changed.loc[t], base.loc[t])

    def test_n3_small_sample_uses_base(self):
        days = _days(n=20)
        etf = _etf(days)
        oas = _oas(days, [3.0 + 0.01 * k for k in range(len(days))])
        nc = proxy.nowcast(oas, etf, days[-5:])
        self.assertTrue(bool((nc["n_train"] < proxy.MIN_TRAIN).all()))
        self.assertTrue(bool((~nc["fitted"]).all()))
        pd.testing.assert_series_equal(nc["oas"], nc["base"], check_names=False)

    def test_n4_exact_recovery(self):
        days = _days(n=140)
        etf = _etf(days)
        vals = [3.0]
        for k in range(1, len(days)):
            d = 2 + 10 * etf["HYG"].iloc[k] - 5 * etf["IEI"].iloc[k]
            vals.append(vals[-1] + d / 100)
        oas = _oas(days, vals)
        dates = days[-5:]
        nc = proxy.nowcast(oas, etf, dates)
        self.assertTrue(bool(nc["fitted"].all()))
        for t in dates:
            self.assertAlmostEqual(nc.loc[t, "oas"], oas.loc[t], delta=1e-9)

    def test_n5_gap_accumulates_days(self):
        days = _days(n=140)
        etf = _etf(days)
        full = _oas(days, [3.0 + 0.001 * k + 0.0005 * (k % 7) for k in range(len(days))])
        t = days[-1]
        oas = full.drop([days[-1], days[-2]])
        nc = proxy.nowcast(oas, etf, [t])
        row = nc.loc[t]
        self.assertTrue(bool(row["fitted"]))
        self.assertEqual(row["base_date"], days[-3])
        train = proxy.daily_pairs(oas, etf)
        train = train[train.index < t]
        b0, b1, b2 = proxy.fit_ols(train)
        pred = sum(b0 + b1 * etf.loc[d, "HYG"] + b2 * etf.loc[d, "IEI"]
                   for d in (days[-2], days[-1]))
        self.assertAlmostEqual(row["oas"], row["base"] + pred / 100, places=9)

    def test_n6_skips_nonconsecutive_pair(self):
        days = _days(n=10)
        etf = _etf(days)
        oas = _oas([days[0], days[1], days[3]], [3.0, 3.01, 3.05])
        pairs = proxy.daily_pairs(oas, etf)
        self.assertIn(days[1], pairs.index)
        self.assertNotIn(days[3], pairs.index)
        self.assertAlmostEqual(pairs.loc[days[1], "d_bp"], 1.0)

    def test_n7_oracle_uses_t_obs(self):
        days = _days(n=20)
        oas = _oas(days, [3.0 + 0.01 * k for k in range(len(days))])
        t = days[15]
        oc = proxy.oracle(oas, [t])
        self.assertEqual(oc.loc[t, "oas_date"], t)
        self.assertEqual(oc.loc[t, "oas"], oas.loc[t])
        self.assertAlmostEqual(oc.loc[t, "d10_bp"], (oas.loc[t] - oas.loc[days[5]]) * 100)
        oc2 = proxy.oracle(oas.drop([t]), [t])
        self.assertEqual(oc2.loc[t, "oas_date"], days[14])
        self.assertEqual(oc2.loc[t, "oas"], oas.loc[days[14]])
        self.assertAlmostEqual(oc2.loc[t, "d10_bp"], (oas.loc[days[14]] - oas.loc[days[4]]) * 100)

    def test_e1_d10_includes_t_and_nan(self):
        days = _days(n=30)
        etf = _etf(days)
        coef = np.array([1.0, 10.0, -5.0])
        sig = proxy.etf_signal(etf, coef, days[0], 3.0, days, window=10)
        p = proxy.pred_change_bp(etf, coef)
        t = days[15]
        i = days.index(t)
        expected = float(p.loc[days[i - 9:i + 1]].sum())
        self.assertAlmostEqual(sig.loc[t, "d10_bp"], expected, places=9)
        self.assertAlmostEqual(sig.loc[t, "pred_bp"], p.loc[t], places=12)
        self.assertTrue(pd.isna(sig.loc[days[8], "d10_bp"]))
        etf2 = etf.copy()
        etf2.loc[days[12], "HYG"] = float("nan")
        sig2 = proxy.etf_signal(etf2, coef, days[0], 3.0, days, window=10)
        self.assertTrue(pd.isna(sig2.loc[days[12], "d10_bp"]))
        self.assertTrue(pd.isna(sig2.loc[t, "d10_bp"]))
        t2 = days[22]
        i2 = days.index(t2)
        p2 = proxy.pred_change_bp(etf2, coef)
        expected2 = float(p2.loc[days[i2 - 9:i2 + 1]].sum())
        self.assertAlmostEqual(sig2.loc[t2, "d10_bp"], expected2, places=9)

    def test_e2_level_anchor(self):
        days = _days(n=30)
        etf = _etf(days)
        coef = np.array([2.0, 10.0, -5.0])
        anchor = days[15]
        anchor_val = 3.5
        sig = proxy.etf_signal(etf, coef, anchor, anchor_val, days, window=10)
        p = proxy.pred_change_bp(etf, coef)
        self.assertAlmostEqual(sig.loc[anchor, "oas"], anchor_val, places=12)
        self.assertAlmostEqual(sig.loc[days[16], "oas"], anchor_val + p.loc[days[16]] / 100, places=9)
        self.assertAlmostEqual(sig.loc[days[14], "oas"], anchor_val - p.loc[anchor] / 100, places=9)

    def test_e3_fit_fixed_ignores_outside(self):
        days = _days(n=140)
        etf = _etf(days)
        oas = _oas(days, [3.0 + 0.001 * k for k in range(len(days))])
        start, end = days[50], days[100]
        base = proxy.fit_fixed(oas, etf, start, end)
        mod = oas.copy()
        mod.loc[days[10]] = 99.0
        mod.loc[days[120]] = -99.0
        changed = proxy.fit_fixed(mod, etf, start, end)
        for b, c in zip(base, changed):
            self.assertAlmostEqual(b, c, places=9)

    def test_e4_anchor_missing_raises(self):
        days = _days(n=30)
        etf = _etf(days)
        coef = np.array([0.0, 1.0, 1.0])
        with self.assertRaises(ValueError):
            proxy.etf_signal(etf, coef, "19990101", 3.0, days)


if __name__ == "__main__":
    unittest.main()
