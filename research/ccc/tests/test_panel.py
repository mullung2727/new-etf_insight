"""합성 데이터로 panel 검증 (C1,C2,C4,C7,C10,C12,C13,C14)."""

import sqlite3
import unittest

import pandas as pd

from research.ccc.panel import _concept_ids, asof, ccc_table, quarter_flows


def _df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["cik", "tag", "start", "end", "filed", "val"])
    df["start"] = pd.to_datetime(df["start"].replace("", None))
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    df["val"] = df["val"].astype(float)
    return df


def _c1_rows(cik="0000000001", assets_lag=10000.0):
    return [
        (cik, "Revenues", "2020-01-01", "2020-03-31", "2020-05-01", 300.0),
        (cik, "CostOfGoodsSold", "2020-01-01", "2020-03-31", "2020-05-01", 200.0),
        (cik, "InventoryNet", "", "2020-03-31", "2020-05-01", 100.0),
        (cik, "InventoryNet", "", "2019-12-31", "2020-02-01", 120.0),
        (cik, "AccountsReceivableNetCurrent", "", "2020-03-31", "2020-05-01", 50.0),
        (cik, "AccountsReceivableNetCurrent", "", "2019-12-31", "2020-02-01", 70.0),
        (cik, "AccountsPayableCurrent", "", "2020-03-31", "2020-05-01", 40.0),
        (cik, "AccountsPayableCurrent", "", "2019-12-31", "2020-02-01", 60.0),
        (cik, "Assets", "", "2019-12-31", "2020-02-01", assets_lag),
        (cik, "StockholdersEquity", "", "2020-03-31", "2020-05-01", 5000.0),
    ]


class TestPanel(unittest.TestCase):
    def test_c1_ccc_formula(self) -> None:
        out = ccc_table(_df(_c1_rows()))
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out.loc[0, "ccc"], 365 * (110 / 200 + 60 / 300 - 50 / 200))

    def test_c2_q4_annual_minus_9m(self) -> None:
        base = [
            ("0000000001", "Revenues", "2019-01-01", "2019-12-31", "2020-02-01", 1000.0),
            ("0000000001", "Revenues", "2019-01-01", "2019-09-30", "2019-10-25", 700.0),
            ("0000000001", "CostOfGoodsSold", "2019-01-01", "2019-12-31", "2020-02-01", 600.0),
            ("0000000001", "CostOfGoodsSold", "2019-01-01", "2019-09-30", "2019-10-25", 400.0),
        ]
        q = quarter_flows(_df(base))
        self.assertEqual(len(q), 1)
        self.assertEqual(q.loc[0, "sales"], 300.0)
        self.assertEqual(q.loc[0, "cogs"], 200.0)
        direct = base + [
            ("0000000001", "Revenues", "2019-10-01", "2019-12-31", "2020-02-01", 350.0),
            ("0000000001", "CostOfGoodsSold", "2019-10-01", "2019-12-31", "2020-02-01", 250.0),
        ]
        q2 = quarter_flows(_df(direct))
        self.assertEqual(len(q2), 1)
        self.assertEqual(q2.loc[0, "sales"], 350.0)
        self.assertEqual(q2.loc[0, "cogs"], 250.0)
        neg = [
            ("0000000001", "Revenues", "2019-01-01", "2019-12-31", "2020-02-01", 500.0),
            ("0000000001", "Revenues", "2019-01-01", "2019-09-30", "2019-10-25", 700.0),
            ("0000000001", "CostOfGoodsSold", "2019-01-01", "2019-12-31", "2020-02-01", 600.0),
            ("0000000001", "CostOfGoodsSold", "2019-01-01", "2019-09-30", "2019-10-25", 400.0),
        ]
        self.assertTrue(quarter_flows(_df(neg)).empty)

    def test_c4_sales_assets_filter(self) -> None:
        rows = _c1_rows("0000000001", 10000.0) + _c1_rows("0000000002", 20000.0)
        out = ccc_table(_df(rows))
        self.assertEqual(set(out["cik"]), {"0000000001"})

    def test_c7_asof(self) -> None:
        rows = [
            ("0000000001", "Revenues", "2020-01-01", "2020-03-31", "2020-05-01", 300.0),
            ("0000000001", "Revenues", "2020-01-01", "2020-03-31", "2020-07-01", 310.0),
            ("0000000001", "CostOfGoodsSold", "2020-01-01", "2020-03-31", "2020-08-01", 200.0),
        ]
        f = _df(rows)
        a1 = asof(f, pd.Timestamp("2020-06-30"))
        self.assertEqual(len(a1), 1)
        self.assertEqual(float(a1.loc[a1["tag"] == "Revenues", "val"].iloc[0]), 300.0)
        a2 = asof(f, pd.Timestamp("2020-08-01"))
        self.assertEqual(float(a2.loc[a2["tag"] == "Revenues", "val"].iloc[0]), 310.0)
        self.assertIn("CostOfGoodsSold", set(a2["tag"]))

    def test_c10_tag_priority(self) -> None:
        rows = [
            ("0000000001", "Revenues", "2020-01-01", "2020-03-31", "2020-05-01", 300.0),
            ("0000000001", "RevenueFromContractWithCustomerExcludingAssessedTax",
             "2020-01-01", "2020-03-31", "2020-05-01", 999.0),
            ("0000000001", "CostOfGoodsSold", "2020-01-01", "2020-03-31", "2020-05-01", 200.0),
        ]
        q = quarter_flows(_df(rows))
        self.assertEqual(len(q), 1)
        self.assertEqual(q.loc[0, "sales"], 300.0)

    def test_c12_concept_taxonomy(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE TABLE concepts(concept_id INTEGER PRIMARY KEY, taxonomy TEXT, tag TEXT, label TEXT)")
            con.executemany("INSERT INTO concepts VALUES (?,?,?,?)", [
                (1, "us-gaap", "Assets", ""),
                (2, "ifrs-full", "Assets", ""),
                (3, "us-gaap", "Revenues", ""),
                (4, "dei", "EntityCommonStockSharesOutstanding", ""),
                (5, "us-gaap", "FooBar", ""),
            ])
            m = _concept_ids(con)
        finally:
            con.close()
        self.assertIn(1, m)
        self.assertIn(3, m)
        self.assertIn(4, m)
        self.assertNotIn(2, m)
        self.assertNotIn(5, m)
        self.assertEqual(m[1], "Assets")

    def test_c13_ar_fallback_tag(self) -> None:
        rows = [r if r[1] != "AccountsReceivableNetCurrent"
                else (r[0], "ReceivablesNetCurrent", *r[2:])
                for r in _c1_rows()]
        out = ccc_table(_df(rows))
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out.loc[0, "ccc"], 365 * (110 / 200 + 60 / 300 - 50 / 200))

    def test_c14_ar_priority(self) -> None:
        rows = _c1_rows() + [
            ("0000000001", "ReceivablesNetCurrent", "", "2020-03-31", "2020-05-01", 999.0),
            ("0000000001", "ReceivablesNetCurrent", "", "2019-12-31", "2020-02-01", 888.0),
        ]
        out = ccc_table(_df(rows))
        self.assertEqual(len(out), 1)
        self.assertEqual(out.loc[0, "ar"], 50.0)
        self.assertEqual(out.loc[0, "ar_beg"], 70.0)


if __name__ == "__main__":
    unittest.main()
