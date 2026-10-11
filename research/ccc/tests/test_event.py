"""합성 데이터로 event 검증 (E1~E7)."""

import unittest

import duckdb
import pandas as pd

from research.ccc.event import build_events, event_windows, first_reported, summarize


def _df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["cik", "tag", "start", "end", "filed", "val"])
    df["start"] = pd.to_datetime(df["start"].replace("", None))
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df["val"] = df["val"].astype(float)
    return df


def _cik_rows(cik, inv=100.0, inv_beg=120.0, ar=50.0, ar_beg=70.0, ap=40.0, ap_beg=60.0,
              assets_lag=10000.0, equity=5000.0, sales=300.0, cogs=200.0, shares=1000.0,
              q0="2020-01-01", q1="2020-03-31", filed="2020-05-01",
              beg="2019-12-31", beg_filed="2020-02-01"):
    return [
        (cik, "Revenues", q0, q1, filed, sales),
        (cik, "CostOfGoodsSold", q0, q1, filed, cogs),
        (cik, "InventoryNet", "", q1, filed, inv),
        (cik, "InventoryNet", "", beg, beg_filed, inv_beg),
        (cik, "AccountsReceivableNetCurrent", "", q1, filed, ar),
        (cik, "AccountsReceivableNetCurrent", "", beg, beg_filed, ar_beg),
        (cik, "AccountsPayableCurrent", "", q1, filed, ap),
        (cik, "AccountsPayableCurrent", "", beg, beg_filed, ap_beg),
        (cik, "Assets", "", beg, beg_filed, assets_lag),
        (cik, "StockholdersEquity", "", q1, filed, equity),
        (cik, "EntityCommonStockSharesOutstanding", "", q1, filed, shares),
    ]


def _ohlcv(rows):
    con = duckdb.connect()
    con.execute("CREATE TABLE ohlcv(ticker VARCHAR, date VARCHAR, close DOUBLE)")
    con.executemany("INSERT INTO ohlcv VALUES (?, ?, ?)", rows)
    return con


def _ev(ticker, avail):
    return pd.DataFrame({"event_id": [0], "ticker": [ticker],
                         "avail": [pd.Timestamp(avail)]})


class TestEvent(unittest.TestCase):
    def test_e1_first_reported_keeps_earliest(self) -> None:
        rows = _cik_rows("0000000001")
        rows.append(("0000000001", "Revenues", "2020-01-01", "2020-03-31", "2020-06-01", 999.0))
        rows.append(("0000000001", "InventoryNet", "", "2020-03-31", "2020-06-01", 999.0))
        out = first_reported(_df(rows))
        rev = out[(out["tag"] == "Revenues")]
        self.assertEqual(len(rev), 1)
        self.assertAlmostEqual(rev.iloc[0]["val"], 300.0)
        inv = out[(out["tag"] == "InventoryNet") & (out["end"] == "2020-03-31")]
        self.assertEqual(len(inv), 1)
        self.assertAlmostEqual(inv.iloc[0]["val"], 100.0)

    def test_e2_day0_skips_holiday(self) -> None:
        con = _ohlcv([
            ("AAA", "20200102", 100.0), ("AAA", "20200103", 110.0), ("AAA", "20200106", 132.0),
            ("BBB", "20200102", 50.0), ("BBB", "20200103", 50.0), ("BBB", "20200106", 50.0),
        ])
        try:
            out = event_windows(_ev("AAA", "2020-01-04"), con, lo=-1, hi=0)
        finally:
            con.close()
        self.assertEqual(set(out["k"]), {-1, 0})
        r = out.set_index("k")["ar"].to_dict()
        self.assertAlmostEqual(r[-1], (110 / 100 - 1) / 2)
        self.assertAlmostEqual(r[0], (132 / 110 - 1) / 2)

    def test_e3_ar_matches_hand_calc(self) -> None:
        con = _ohlcv([
            ("AAA", "20200102", 100.0), ("AAA", "20200103", 110.0), ("AAA", "20200104", 121.0),
            ("BBB", "20200102", 200.0), ("BBB", "20200103", 200.0), ("BBB", "20200104", 200.0),
        ])
        try:
            out = event_windows(_ev("AAA", "2020-01-02"), con, lo=0, hi=2)
        finally:
            con.close()
        self.assertEqual(set(out["k"]), {1, 2})
        for v in out["ar"]:
            self.assertAlmostEqual(v, (110 / 100 - 1) - ((110 / 100 - 1) + 0) / 2)

    def test_e4_clip_at_plus_100pct(self) -> None:
        con = _ohlcv([
            ("AAA", "20200102", 100.0), ("AAA", "20200103", 400.0),
            ("BBB", "20200102", 100.0), ("BBB", "20200103", 100.0),
        ])
        try:
            out = event_windows(_ev("AAA", "2020-01-02"), con, lo=0, hi=1)
        finally:
            con.close()
        self.assertEqual(set(out["k"]), {1})
        self.assertAlmostEqual(out.iloc[0]["ar"], 1.0 - (1.0 + 0) / 2)

    def test_e5_lag_filter_drops_late_event(self) -> None:
        rows, cs = [], []
        for i in range(11):
            cik = f"{i + 1:010d}"
            kw = {"filed": "2020-08-28"} if i == 0 else {}
            rows += _cik_rows(cik, inv=100.0 + i, **kw)
            cs.append({"cik": cik, "ticker": f"L{i:02d}", "sic": 3000})
        out = build_events(_df(rows), pd.DataFrame(cs), {(48, 3000): 1})
        self.assertEqual(len(out), 10)
        self.assertNotIn("0000000001", set(out["cik"]))

    def test_e6_deciles_per_quarter(self) -> None:
        rows, cs = [], []
        for i in range(10):
            cik = f"{i + 1:010d}"
            rows += _cik_rows(cik, inv=100.0 + i)
            cs.append({"cik": cik, "ticker": f"Q{i:02d}", "sic": 3000})
        q2 = dict(q0="2020-04-01", q1="2020-06-30", filed="2020-08-01",
                  beg="2020-03-31", beg_filed="2020-05-01")
        for i in range(10):
            cik = f"{i + 11:010d}"
            rows += _cik_rows(cik, inv=100.0 + i, **q2)
            cs.append({"cik": cik, "ticker": f"R{i:02d}", "sic": 3000})
        out = build_events(_df(rows), pd.DataFrame(cs), {(48, 3000): 1})
        self.assertEqual(len(out), 20)
        self.assertEqual(set(out["aq"]), {20202, 20203})
        for aq, g in out.groupby("aq"):
            self.assertEqual(set(g["dec"]), set(range(1, 11)), aq)

    def test_e7_spread_mean_matches_hand_calc(self) -> None:
        ev = pd.DataFrame({
            "event_id": [0, 1, 2, 3, 4, 5],
            "aq": [20201, 20201, 20202, 20202, 20203, 20203],
            "dec": [1, 10, 1, 10, 1, 10],
            "avail": pd.to_datetime(["2020-02-01"] * 2 + ["2020-05-01"] * 2
                                    + ["2020-08-01"] * 2),
        })
        ar = pd.DataFrame({
            "event_id": [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5],
            "k": [0, 1] * 6,
            "ar": [0.01, 0.02, 0.005, 0.005, 0.02, 0.02, 0.005, 0.005,
                   0.03, 0.02, 0.01, 0.01],
        })
        res = summarize(ev, ar)
        t = res["table"]
        row = t[(t["period"] == "ALL") & (t["bucket"] == "(0,1)")].iloc[0]
        self.assertAlmostEqual(row["spread"], (0.02 + 0.03 + 0.03) / 3)
        self.assertEqual(int(row["nq"]), 3)
        self.assertEqual(int(row["n_ev"]), 6)

    def test_e8_rank_within_ind(self) -> None:
        rows, cs = [], []
        for i in range(10):
            cik = f"{i + 1:010d}"
            rows += _cik_rows(cik, inv=100.0 + i * 200.0)
            cs.append({"cik": cik, "ticker": f"A{i:02d}", "sic": 3000})
        for i in range(10):
            cik = f"{i + 11:010d}"
            rows += _cik_rows(cik, inv=100.0 + i * 2.0)
            cs.append({"cik": cik, "ticker": f"B{i:02d}", "sic": 4000})
        out = build_events(_df(rows), pd.DataFrame(cs), {(48, 3000): 1, (48, 4000): 2},
                           rank_within_ind=True)
        self.assertEqual(len(out), 20)
        ind_of = {c["cik"]: (1 if c["sic"] == 3000 else 2) for c in cs}
        d1 = out[out["dec"] == 1]
        d10 = out[out["dec"] == 10]
        self.assertEqual(len(d1), 2)
        self.assertEqual(len(d10), 2)
        self.assertEqual(set(d1["cik"].map(ind_of)), {1, 2})
        self.assertEqual(set(d10["cik"].map(ind_of)), {1, 2})


if __name__ == "__main__":
    unittest.main()
