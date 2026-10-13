"""us_fin_std 합성 테스트 (DB 없음)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest

import pandas as pd

from research.afa.items import ttm_obs
from research.us_fin_std.build import _read_tags, pick_priority, std_snapshot
from research.us_fin_std.mapping import GROUPS, tag_to_item

T = pd.Timestamp("2020-04-01")


def _facts(rows) -> pd.DataFrame:
    """합성 facts."""
    df = pd.DataFrame(rows, columns=["cik", "tag", "start", "end", "filed", "val"])
    for c in ("start", "end", "filed"):
        df[c] = pd.to_datetime(df[c])
    return df


def _long(rows) -> pd.DataFrame:
    """합성 long (cik, tag, end, avail, val)."""
    df = pd.DataFrame(rows, columns=["cik", "tag", "end", "avail", "val"])
    for c in ("end", "avail"):
        df[c] = pd.to_datetime(df[c])
    return df


def _empty_long() -> pd.DataFrame:
    return pd.DataFrame(columns=["cik", "tag", "end", "avail", "val"])


class TestMapping(unittest.TestCase):
    def test_s0_no_dup_kind(self):
        """태그 중복 없음, kind ∈ {bal, flow}."""
        m = tag_to_item()
        total = sum(len(g["tags"]) for g in GROUPS.values())
        self.assertEqual(len(m), total)
        for g in GROUPS.values():
            self.assertIn(g["kind"], {"bal", "flow"})


class TestPickPriority(unittest.TestCase):
    def test_s1_priority(self):
        """같은 cik·기간·filed → 우선순위 첫 태그."""
        out = pick_priority(_facts([
            (1, "Revenues", "2019-01-01", "2019-12-31", "2020-02-01", 100.0),
            (1, "SalesRevenueNet", "2019-01-01", "2019-12-31", "2020-02-01", 90.0),
        ]))
        self.assertEqual(len(out), 1)
        self.assertEqual(out["item"].iloc[0], "revenue")
        self.assertEqual(float(out["val"].iloc[0]), 100.0)
        self.assertEqual(out["src_tag"].iloc[0], "Revenues")

    def test_s1b_first_reported_wins(self):
        """먼저 공시된 태그가 우선순위보다 앞섬."""
        out = pick_priority(_facts([
            (1, "SalesRevenueNet", "2019-01-01", "2019-12-31", "2020-01-15", 90.0),
            (1, "Revenues", "2019-01-01", "2019-12-31", "2020-02-01", 100.0),
        ]))
        self.assertEqual(len(out), 1)
        self.assertEqual(float(out["val"].iloc[0]), 90.0)
        self.assertEqual(out["src_tag"].iloc[0], "SalesRevenueNet")

    def test_s2_tag_switch(self):
        """연도별 다른 태그 → 둘 다 revenue, TTM 양쪽 end."""
        picked = pick_priority(_facts([
            (1, "SalesRevenueNet", "2017-01-01", "2017-12-31", "2018-02-01", 50.0),
            (1, "RevenueFromContractWithCustomerExcludingAssessedTax",
             "2019-01-01", "2019-12-31", "2020-02-01", 70.0),
        ]))
        self.assertEqual(set(picked["item"]), {"revenue"})
        out = ttm_obs(picked.rename(columns={"item": "tag"}))
        ends = set(out["end"])
        self.assertIn(pd.Timestamp("2017-12-31"), ends)
        self.assertIn(pd.Timestamp("2019-12-31"), ends)

    def test_s6_excluded_tag(self):
        """범위 다른 태그는 버림."""
        out = pick_priority(_facts([
            (1, "InventoryGross", "2019-01-01", "2019-12-31", "2020-02-01", 10.0),
        ]))
        self.assertTrue(out.empty)


class TestStdSnapshot(unittest.TestCase):
    def test_s3_bal_zero(self):
        """총자산 있으면 우선주 0, 없으면 NaN."""
        bal = _long([(1, "total_assets", "2019-12-31", "2020-02-01", 1000.0)])
        ttm = _long([(2, "revenue", "2019-12-31", "2020-02-01", 500.0)])
        s = std_snapshot(bal, ttm, T)
        self.assertEqual(float(s.loc[1, "preferred_stock"]), 0.0)
        self.assertTrue(bool(pd.isna(s.loc[2, "preferred_stock"])))

    def test_s3b_flow_zero(self):
        """매출 있으면 연구개발비 0, 둘 다 없으면 NaN."""
        bal = _long([(2, "total_assets", "2019-12-31", "2020-02-01", 800.0)])
        ttm = _long([(1, "revenue", "2019-12-31", "2020-02-01", 500.0)])
        s = std_snapshot(bal, ttm, T)
        self.assertEqual(float(s.loc[1, "research_and_development"]), 0.0)
        self.assertTrue(bool(pd.isna(s.loc[2, "research_and_development"])))

    def test_s4_no_zero_core(self):
        """매출 결측은 그대로 NaN."""
        bal = _long([(1, "total_assets", "2019-12-31", "2020-02-01", 1000.0)])
        s = std_snapshot(bal, _empty_long(), T)
        self.assertTrue(bool(pd.isna(s.loc[1, "revenue"])))

    def test_s5_computed(self):
        """자본 합산 → 총부채 차감."""
        bal = _long([
            (1, "liabilities_and_equity", "2019-12-31", "2020-02-01", 100.0),
            (1, "stockholders_equity", "2019-12-31", "2020-02-01", 40.0),
            (1, "minority_interest", "2019-12-31", "2020-02-01", 5.0),
        ])
        s = std_snapshot(bal, _empty_long(), T)
        self.assertAlmostEqual(float(s.loc[1, "total_equity"]), 45.0)
        self.assertAlmostEqual(float(s.loc[1, "total_liabilities"]), 55.0)

    def test_s7_timing(self):
        """avail > t 값은 안 보임."""
        bal = _long([
            (1, "total_assets", "2019-12-31", "2020-05-01", 1000.0),
            (2, "total_assets", "2019-12-31", "2020-02-01", 2000.0),
        ])
        s = std_snapshot(bal, _empty_long(), T)
        self.assertTrue(s.empty or 1 not in s.index
                        or bool(pd.isna(s.loc[1, "total_assets"])))
        self.assertEqual(float(s.loc[2, "total_assets"]), 2000.0)

    def test_s8_fallback_window(self):
        """200일 밖 연간 보고는 400일 대체 창으로 보임, 최근값 우선."""
        old_end = (T - pd.Timedelta(days=300)).strftime("%Y-%m-%d")
        old_avail = (T - pd.Timedelta(days=260)).strftime("%Y-%m-%d")
        new_end = (T - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
        new_avail = (T - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        bal = _long([
            (1, "total_assets", old_end, old_avail, 1000.0),
            (2, "total_assets", old_end, old_avail, 1000.0),
            (2, "total_assets", new_end, new_avail, 2000.0),
        ])
        s = std_snapshot(bal, _empty_long(), T)
        self.assertEqual(float(s.loc[1, "total_assets"]), 1000.0)
        self.assertEqual(float(s.loc[2, "total_assets"]), 2000.0)

    def test_s9_tax_fill(self):
        """income_before_xo + tax → income_before_tax."""
        ttm = _long([
            (1, "income_before_xo", "2019-12-31", "2020-02-01", 80.0),
            (1, "income_tax_expense", "2019-12-31", "2020-02-01", 20.0),
        ])
        s = std_snapshot(_empty_long(), ttm, T)
        self.assertAlmostEqual(float(s.loc[1, "income_before_tax"]), 100.0)


class TestReadTags(unittest.TestCase):
    def test_only_requested_usd_valid(self):
        """요청 태그 USD 유효행만, 다른 태그·다른 unit·잘못된 날짜 제외."""
        with tempfile.TemporaryDirectory() as d:
            db = f"{d}/t.sqlite3"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE concepts (concept_id INTEGER PRIMARY KEY, taxonomy TEXT, tag TEXT)")
            con.execute("CREATE TABLE facts (cik INTEGER, concept_id INTEGER, unit TEXT, start TEXT, end TEXT, filed TEXT, val REAL)")
            con.executemany("INSERT INTO concepts VALUES (?, ?, ?)",
                            [(1, "us-gaap", "Revenues"), (2, "us-gaap", "Assets")])
            con.executemany("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(1, 1, "USD", "2019-01-01", "2019-12-31", "2020-02-01", 100.0),
                             (1, 2, "USD", None, "2019-12-31", "2020-02-01", 500.0),
                             (1, 1, "shares", "2019-01-01", "2019-12-31", "2020-02-01", 1.0e8),
                             (1, 1, "USD", "0202-02-01", "2019-12-31", "2020-02-01", 70.0)])
            con.commit()
            con.close()
            out = _read_tags(db, ("Revenues",))
        self.assertEqual(len(out), 1)
        self.assertEqual(out["tag"].iloc[0], "Revenues")
        self.assertAlmostEqual(float(out["val"].iloc[0]), 100.0)


if __name__ == "__main__":
    unittest.main()
