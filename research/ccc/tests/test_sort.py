"""합성 데이터로 sort 검증 (C3,C5,C6,C8,C9,C11,C15)."""

import unittest

import numpy as np
import pandas as pd

from research.ccc.sort import cross_section, decile_returns, formation_window, nw_tstat


def _df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["cik", "tag", "start", "end", "filed", "val"])
    df["start"] = pd.to_datetime(df["start"].replace("", None))
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df["val"] = df["val"].astype(float)
    return df


def _cik_rows(cik, inv=100.0, inv_beg=120.0, ar=50.0, ar_beg=70.0, ap=40.0, ap_beg=60.0,
              assets_lag=10000.0, equity=5000.0, sales=300.0, cogs=200.0, shares=1000.0):
    return [
        (cik, "Revenues", "2020-01-01", "2020-03-31", "2020-05-01", sales),
        (cik, "CostOfGoodsSold", "2020-01-01", "2020-03-31", "2020-05-01", cogs),
        (cik, "InventoryNet", "", "2020-03-31", "2020-05-01", inv),
        (cik, "InventoryNet", "", "2019-12-31", "2020-02-01", inv_beg),
        (cik, "AccountsReceivableNetCurrent", "", "2020-03-31", "2020-05-01", ar),
        (cik, "AccountsReceivableNetCurrent", "", "2019-12-31", "2020-02-01", ar_beg),
        (cik, "AccountsPayableCurrent", "", "2020-03-31", "2020-05-01", ap),
        (cik, "AccountsPayableCurrent", "", "2019-12-31", "2020-02-01", ap_beg),
        (cik, "Assets", "", "2019-12-31", "2020-02-01", assets_lag),
        (cik, "StockholdersEquity", "", "2020-03-31", "2020-05-01", equity),
        (cik, "EntityCommonStockSharesOutstanding", "", "2020-03-31", "2020-05-01", shares),
    ]


class TestSort(unittest.TestCase):
    def test_c3_financial_excluded(self) -> None:
        facts = _df(_cik_rows("0000000001") + _cik_rows("0000000002"))
        comp = pd.DataFrame([{"cik": "0000000001", "ticker": "AAA", "sic": 5000},
                             {"cik": "0000000002", "ticker": "BBB", "sic": 6500}])
        rets = pd.DataFrame([{"ticker": "AAA", "ym": 202007, "ret": 0.01, "prev_close": 10.0, "prev_raw_close": 10.0, "market": "NYSE"},
                             {"ticker": "BBB", "ym": 202007, "ret": 0.01, "prev_close": 10.0, "prev_raw_close": 10.0, "market": "NYSE"}])
        out = cross_section(202007, facts, rets, comp, {(48, 5000): 1, (48, 6500): 2}, 48)
        self.assertEqual(set(out["cik"]), {"0000000001"})

    def test_c5_winsorize(self) -> None:
        rows, cs, rs = [], [], []
        for i in range(101):
            cik = f"{i + 1:010d}"
            rows += _cik_rows(cik, inv=100.0 + i, inv_beg=100.0, ar=50.0, ar_beg=50.0,
                              ap=40.0, ap_beg=40.0)
            cs.append({"cik": cik, "ticker": f"T{i:03d}", "sic": 3000})
            rs.append({"ticker": f"T{i:03d}", "ym": 202007, "ret": 0.01,
                       "prev_close": 10.0, "prev_raw_close": 10.0, "market": "NYSE"})
        out = cross_section(202007, _df(rows), pd.DataFrame(rs), pd.DataFrame(cs), {(48, 3000): 1}, 48)
        self.assertEqual(len(out), 101)
        o = out.set_index("cik")["ccc"].to_dict()

        def exp(inv):
            return 365 * ((inv + 100) / 2 / 200 + 50 / 300 - 40 / 200)

        self.assertAlmostEqual(o["0000000001"], exp(101.0))
        self.assertAlmostEqual(o["0000000101"], exp(199.0))
        self.assertAlmostEqual(o["0000000051"], exp(150.0))

    def test_c6_formation_window(self) -> None:
        d, lo, hi = formation_window(202007)
        self.assertEqual((d, lo, hi), (pd.Timestamp("2020-06-30"), pd.Timestamp("2020-01-01"),
                                      pd.Timestamp("2020-03-31")))
        q = pd.Timestamp("2020-03-31")
        for ym in (202007, 202008, 202009):
            _, lo, hi = formation_window(ym)
            self.assertTrue(lo <= q <= hi, ym)
        for ym in (202006, 202010):
            _, lo, hi = formation_window(ym)
            self.assertFalse(lo <= q <= hi, ym)

    def test_c8_vw_uses_prev_close_me(self) -> None:
        facts = _df(_cik_rows("0000000001", shares=100.0))
        comp = pd.DataFrame([{"cik": "0000000001", "ticker": "AAA", "sic": 3000}])
        rets = pd.DataFrame([{"ticker": "AAA", "ym": 202007, "ret": 1.0,
                              "prev_close": 10.0, "prev_raw_close": 40.0, "market": "NYSE"}])
        out = cross_section(202007, facts, rets, comp, {(48, 3000): 1}, 48)
        self.assertAlmostEqual(out.loc[0, "me"], 4000.0)
        cs = pd.DataFrame({"x": list(range(1, 21)), "ret": [0.0, 1.0] + [0.0] * 18,
                           "me": [1.0, 3.0] + [1.0] * 18})
        self.assertAlmostEqual(decile_returns(cs, "x", "ew").loc[1], 0.5)
        self.assertAlmostEqual(decile_returns(cs, "x", "vw").loc[1], 0.75)

    def test_c9_ind_median_adj(self) -> None:
        rows, cs, rs = [], [], []
        for i in range(6):
            cik = f"{i + 1:010d}"
            sic = 1000 if i < 3 else 2000
            rows += _cik_rows(cik, inv=100.0 + i * 10)
            cs.append({"cik": cik, "ticker": f"S{i}", "sic": sic})
            rs.append({"ticker": f"S{i}", "ym": 202007, "ret": 0.01,
                       "prev_close": 10.0, "prev_raw_close": 10.0, "market": "NYSE"})
        out = cross_section(202007, _df(rows), pd.DataFrame(rs), pd.DataFrame(cs),
                            {(48, 1000): 1, (48, 2000): 2}, 48)
        self.assertEqual(len(out), 6)
        for ind, g in out.groupby("ind"):
            med = g["ccc"].median()
            for _, r in g.iterrows():
                self.assertAlmostEqual(r["ccc_adj"], r["ccc"] - med)
            self.assertAlmostEqual(g["ccc_adj"].median(), 0.0)

    def test_c15_me_uses_prev_raw_close(self) -> None:
        facts = _df(_cik_rows("0000000001", shares=50.0))
        comp = pd.DataFrame([{"cik": "0000000001", "ticker": "AAA", "sic": 3000}])
        rets = pd.DataFrame([{"ticker": "AAA", "ym": 202007, "ret": 0.01,
                              "prev_close": 2.0, "prev_raw_close": 200.0, "market": "NYSE"}])
        out = cross_section(202007, facts, rets, comp, {(48, 3000): 1}, 48)
        self.assertAlmostEqual(out.loc[0, "me"], 10000.0)

    def test_microcap_breakpoint_uses_all_nyse(self) -> None:
        rows = _cik_rows("0000000001", shares=10.0)
        for i in range(5):
            cik = f"{i + 2:010d}"
            rows.append((cik, "EntityCommonStockSharesOutstanding", "", "2020-03-31", "2020-05-01", 10000.0))
        facts = _df(rows)
        cs = [{"cik": "0000000001", "ticker": "AAA", "sic": 3000}]
        rs = [{"ticker": "AAA", "ym": 202007, "ret": 0.01,
               "prev_close": 10.0, "prev_raw_close": 10.0, "market": "NYSE"}]
        for i in range(5):
            cik = f"{i + 2:010d}"
            cs.append({"cik": cik, "ticker": f"BIG{i}", "sic": 3000})
            rs.append({"ticker": f"BIG{i}", "ym": 202007, "ret": 0.01,
                       "prev_close": 100.0, "prev_raw_close": 100.0 * (i + 1), "market": "NYSE"})
        out = cross_section(202007, facts, pd.DataFrame(rs), pd.DataFrame(cs), {(48, 3000): 1}, 48)
        self.assertEqual(len(out), 1)
        self.assertTrue(bool(out.loc[0, "microcap"]))
        self.assertEqual(int(out.loc[0, "size_q"]), 1)

    def test_c11_nw_lag0(self) -> None:
        y = np.arange(1, 51, dtype=float)
        b, t = nw_tstat(y, lags=0)
        self.assertAlmostEqual(float(b[0]), float(y.mean()))
        classic = float(y.mean() / (y.std(ddof=1) / np.sqrt(len(y))))
        self.assertLess(abs(float(t[0]) - classic) / abs(classic), 0.05)


if __name__ == "__main__":
    unittest.main()
