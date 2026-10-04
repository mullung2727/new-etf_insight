"""macro_signals 단위 테스트 — 합성 데이터만, 실 DB·네트워크 없음 (unittest)."""
import unittest

import pandas as pd

from research.us_oas import macro_signals as ms


def _daily(vals, start="2023-01-01"):
    base = pd.Timestamp(start)
    idx = [(base + pd.Timedelta(days=k)).strftime("%Y%m%d") for k in range(len(vals))]
    return pd.Series([float(v) for v in vals], index=idx)


def _next_day(day):
    return (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y%m%d")


def _first(dates, values, rss):
    return pd.DataFrame({"date": list(dates),
                         "value": [float(v) for v in values],
                         "realtime_start": list(rss),
                         "backfilled": [False] * len(dates)})


def _weekly(values, last="2009-04-25"):
    end = pd.Timestamp(last)
    idx = [(end - pd.Timedelta(weeks=k)).strftime("%Y%m%d")
           for k in reversed(range(len(values)))]
    return pd.Series([float(v) for v in values], index=idx)


def _unrate(values, start="2020-01-01"):
    months = pd.date_range(start, periods=len(values), freq="MS")
    dates = [d.strftime("%Y%m%d") for d in months]
    rss = [(d + pd.DateOffset(months=1)).strftime("%Y%m") + "05" for d in months]
    return _first(dates, values, rss)


class TestCurve(unittest.TestCase):
    def test_m1_spread_no_lookahead(self):
        dgs = _daily([4.0] * 30)
        dtb = _daily([3.0] * 30)
        dates = dgs.index[10:15].tolist()
        base = ms.curve(dgs, dtb, dates)
        t = dates[2]
        mod_g, mod_b = dgs.copy(), dtb.copy()
        mod_g.loc[t] = 99.0
        mod_b.loc[t] = -99.0
        changed = ms.curve(mod_g, mod_b, dates)
        self.assertAlmostEqual(changed.loc[t, "spread"], base.loc[t, "spread"])
        self.assertAlmostEqual(base.loc[t, "spread"], 1.0)
        self.assertAlmostEqual(changed.loc[dates[3], "spread"], 198.0)

    def test_m1_disinvert(self):
        spread = [-0.5] * 100 + [0.5] * 160
        dgs = _daily([4.0 + s for s in spread])
        dtb = _daily([4.0] * len(spread))
        t = _next_day(dgs.index[-1])
        out = ms.curve(dgs, dtb, [t])
        self.assertAlmostEqual(out.loc[t, "spread"], 0.5)
        self.assertTrue(out.loc[t, "inv_12m"])
        self.assertTrue(out.loc[t, "disinvert"])
        flat = ms.curve(_daily([4.5] * 260), _daily([4.0] * 260), [t])
        self.assertFalse(flat.loc[t, "inv_12m"])
        self.assertFalse(flat.loc[t, "disinvert"])
        neg = ms.curve(_daily([3.5] * 260), _daily([4.0] * 260), [t])
        self.assertTrue(neg.loc[t, "inv_12m"])
        self.assertFalse(neg.loc[t, "disinvert"])


class TestSahm(unittest.TestCase):
    def test_m2_known_value(self):
        s = ms.sahm(_unrate([4.0] * 12 + [4.2, 4.4, 4.6]), ["20210501"])
        self.assertAlmostEqual(s.loc["20210501"], 0.4)

    def test_m2_t_day_release_excluded(self):
        first15 = _unrate([4.0] * 12 + [4.2, 4.4, 4.6])
        first16 = pd.concat([first15, _first(["20210401"], [9.0], ["20210501"])],
                            ignore_index=True)
        s = ms.sahm(first16, ["20210501"])
        self.assertAlmostEqual(s.loc["20210501"], 0.4)
        s2 = ms.sahm(first16, ["20210502"])
        self.assertGreater(s2.loc["20210502"], 1.0)

    def test_m2_short_history_nan(self):
        s = ms.sahm(_unrate([4.0] * 14), ["20210501"])
        self.assertTrue(pd.isna(s.loc["20210501"]))


class TestClaims(unittest.TestCase):
    def test_m3_approx_uses_obs_plus_12(self):
        obs = _weekly([200.0] * 59 + [400.0], last="2009-04-25")
        last = obs.index[-1]
        t0 = (pd.Timestamp(last) + pd.Timedelta(days=12)).strftime("%Y%m%d")
        t1 = (pd.Timestamp(last) + pd.Timedelta(days=13)).strftime("%Y%m%d")
        out = ms.claims_yoy(_first([], [], []), obs, [t0, t1])
        self.assertTrue(out.loc[t0, "approx"])
        self.assertTrue(out.loc[t1, "approx"])
        self.assertAlmostEqual(out.loc[t0, "yoy"], 0.0)
        self.assertAlmostEqual(out.loc[t1, "yoy"], 0.25)

    def test_m3_first_release_path(self):
        weeks = pd.date_range("2010-01-02", periods=60, freq="W-SAT")
        dates = [d.strftime("%Y%m%d") for d in weeks]
        rss = [(d + pd.Timedelta(days=6)).strftime("%Y%m%d") for d in weeks]
        first = _first(dates, [200.0] * 60, rss)
        obs = pd.Series([999.0] * 60, index=dates)
        t = (weeks[-1] + pd.Timedelta(days=7)).strftime("%Y%m%d")
        out = ms.claims_yoy(first, obs, [t])
        self.assertFalse(out.loc[t, "approx"])
        self.assertAlmostEqual(out.loc[t, "yoy"], 0.0)
        short = ms.claims_yoy(first.iloc[:55], obs, [t])
        self.assertTrue(pd.isna(short.loc[t, "yoy"]))
        self.assertFalse(short.loc[t, "approx"])


class TestPctChange(unittest.TestCase):
    def test_m4_uses_obs_before_t(self):
        lv = _daily([100.0, 110.0, 120.0, 130.0, 140.0])
        dates = lv.index[3:5].tolist()
        out = ms.pct_change_n(lv, dates, n=2)
        self.assertAlmostEqual(out.loc[dates[0]], 0.20)
        self.assertAlmostEqual(out.loc[dates[1]], 130.0 / 110.0 - 1)
        mod = lv.copy()
        mod.loc[dates[0]] = 9999.0
        out2 = ms.pct_change_n(mod, dates, n=2)
        self.assertAlmostEqual(out2.loc[dates[0]], out.loc[dates[0]])
        short = ms.pct_change_n(lv, [lv.index[1]], n=2)
        self.assertTrue(pd.isna(short.iloc[0]))


if __name__ == "__main__":
    unittest.main()
