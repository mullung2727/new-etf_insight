"""us_dividend 합성 테스트 (DB 없음)."""

from __future__ import annotations

import unittest

import pandas as pd

from research.us_dividend.run import (
    cash_covered,
    dd_bucket,
    div_add,
    div_group,
    dps_asof,
    excluded_sic,
    fresh_consecutive,
    payout_bucket,
    select_v1,
    split_factor,
    stable,
    unit_ok,
)

DECLARED = "CommonStockDividendsPerShareDeclared"
PAID = "CommonStockDividendsPerShareCashPaid"


def _dps(rows) -> pd.DataFrame:
    """합성 DPS (cik, tag, end, filed, val)."""
    return pd.DataFrame(rows, columns=["cik", "tag", "end", "filed", "val"])


def _splits(rows) -> pd.DataFrame:
    """합성 분할 (date, ratio)."""
    return pd.DataFrame(rows, columns=["date", "ratio"])


class TestExcludedSic(unittest.TestCase):
    def test_r1(self):
        """6798·6726·None·0·NaN 제외, 2000 통과."""
        for s in (6798, 6726, None, 0, float("nan")):
            self.assertTrue(excluded_sic(s), s)
        self.assertFalse(excluded_sic(2000))


class TestStable(unittest.TestCase):
    def test_r2(self):
        """95% 허용 통과·탈락, 0 포함 탈락, 길이 미달 탈락."""
        self.assertTrue(stable([1.00, 0.96, 0.92]))
        self.assertFalse(stable([1.00, 0.94, 1.00]))
        self.assertFalse(stable([1.0, 0, 1.0]))
        self.assertFalse(stable([1.0, 1.0]))


class TestDpsAsof(unittest.TestCase):
    T = "20240401"

    def test_r3_filed_filter(self):
        """filed == t·> t 미사용, filed < t만 사용."""
        out = dps_asof(_dps([
            ("A", DECLARED, "2023-12-31", "2024-02-01", 1.0),
            ("A", DECLARED, "2022-12-31", "2024-04-01", 9.0),
            ("A", DECLARED, "2021-12-31", "2024-05-01", 8.0),
        ]), self.T)
        self.assertEqual(list(out.columns), ["cik", "end", "val"])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out["val"].iloc[0]), 1.0)

    def test_r3_declared_priority(self):
        """같은 (cik,end)는 Declared 우선."""
        out = dps_asof(_dps([
            ("A", PAID, "2023-12-31", "2024-03-01", 2.0),
            ("A", DECLARED, "2023-12-31", "2024-01-10", 2.5),
        ]), self.T)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out["val"].iloc[0]), 2.5)

    def test_r3_same_tag_latest_filed(self):
        """같은 태그면 filed 가장 늦은 것."""
        out = dps_asof(_dps([
            ("A", DECLARED, "2023-12-31", "2024-01-10", 1.0),
            ("A", DECLARED, "2023-12-31", "2024-02-15", 1.5),
        ]), self.T)
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(float(out["val"].iloc[0]), 1.5)


class TestSplitFactor(unittest.TestCase):
    def test_r4_after(self):
        """filed 이후 4.0 하나 → 4.0."""
        sp = _splits([("20190601", 2.0), ("20210101", 4.0)])
        self.assertAlmostEqual(split_factor(sp, "2020-01-01"), 4.0)

    def test_r4_before_ignored(self):
        """filed 이전 분할은 무시 → 1.0."""
        sp = _splits([("20190601", 2.0)])
        self.assertAlmostEqual(split_factor(sp, "2020-01-01"), 1.0)

    def test_r4_two(self):
        """4.0·0.5 두 개 → 2.0."""
        sp = _splits([("20210101", 4.0), ("20220605", 0.5)])
        self.assertAlmostEqual(split_factor(sp, pd.Timestamp("2020-01-01")), 2.0)

    def test_r4_empty(self):
        """분할 없으면 1.0."""
        self.assertAlmostEqual(
            split_factor(_splits([]), "2020-01-01"), 1.0)


class TestUnitOk(unittest.TestCase):
    def test_r5(self):
        """dps > px*0.5 제외, 경계(==)는 통과."""
        self.assertTrue(unit_ok([1.0, 1.0, 1.0], 10.0))
        self.assertFalse(unit_ok([6.0, 1.0, 1.0], 10.0))
        self.assertTrue(unit_ok([5.0, 1.0, 1.0], 10.0))


class TestCashCovered(unittest.TestCase):
    def test_r6(self):
        """OCF-|capex| >= divpaid, NaN 탈락, capex 음수도 abs."""
        self.assertTrue(cash_covered(100, 30, 60))
        self.assertFalse(cash_covered(100, 50, 60))
        self.assertFalse(cash_covered(float("nan"), 30, 60))
        self.assertFalse(cash_covered(100, float("nan"), 60))
        self.assertFalse(cash_covered(100, 30, float("nan")))
        self.assertTrue(cash_covered(100, -30, 60))


class TestDdBucket(unittest.TestCase):
    def test_r7(self):
        """경계값 구간 배정."""
        self.assertEqual(dd_bucket(-0.20), "0~-20%")
        self.assertEqual(dd_bucket(-0.2001), "-20~-50%")
        self.assertEqual(dd_bucket(-0.50), "-20~-50%")
        self.assertEqual(dd_bucket(-0.5001), "-50%초과")


class TestFreshConsecutive(unittest.TestCase):
    def test_fresh(self):
        """연속 3개·신선하면 True, 오래됨·연도빠짐이면 False."""
        ends = [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31"),
                pd.Timestamp("2024-12-31")]
        self.assertTrue(fresh_consecutive(ends, "2025-04-01"))
        self.assertFalse(fresh_consecutive(ends, "2027-04-01"))
        gap = [pd.Timestamp("2020-12-31"), pd.Timestamp("2022-12-31"),
               pd.Timestamp("2023-12-31")]
        self.assertFalse(fresh_consecutive(gap, "2025-04-01"))


class TestSelectV1(unittest.TestCase):
    def _cand(self) -> pd.DataFrame:
        """합성 후보 (ticker, yld, r1y, ocf, capex, divpaid)."""
        return pd.DataFrame([
            {"ticker": "A", "yld": 0.09, "r1y": -0.6, "ocf": 100.0,
             "capex": 50.0, "divpaid": 60.0},
            {"ticker": "B", "yld": 0.08, "r1y": -0.6, "ocf": 100.0,
             "capex": 30.0, "divpaid": 60.0},
            {"ticker": "C", "yld": 0.07, "r1y": -0.1, "ocf": 100.0,
             "capex": 50.0, "divpaid": 60.0},
            {"ticker": "D", "yld": 0.06, "r1y": float("nan"), "ocf": 100.0,
             "capex": 50.0, "divpaid": 60.0},
            {"ticker": "E", "yld": 0.05, "r1y": -0.3, "ocf": 100.0,
             "capex": 50.0, "divpaid": 60.0},
        ])

    def test_thr_50(self):
        """-0.6&미감당만 제외, 감당·-0.1·NaN은 유지."""
        out = select_v1(self._cand(), -0.50)
        self.assertNotIn("A", out)
        for t in ("B", "C", "D", "E"):
            self.assertIn(t, out)

    def test_thr_20(self):
        """thr -0.20이면 -0.3&미감당도 제외."""
        out = select_v1(self._cand(), -0.20)
        self.assertNotIn("A", out)
        self.assertNotIn("E", out)
        for t in ("B", "C", "D"):
            self.assertIn(t, out)

    def test_top_n(self):
        """yld 상위 n개만."""
        self.assertEqual(select_v1(self._cand(), -0.50, n=2), ["B", "C"])


class TestDivGroup(unittest.TestCase):
    def test_groups(self):
        """①②③④ 각 1케이스 + 안정·미감당 → ②."""
        self.assertEqual(
            div_group(True, [1.0, 1.0, 1.0], False, 50.0, True),
            "①안정+현금감당")
        self.assertEqual(
            div_group(True, [1.0, 1.0, 1.0], False, 50.0, False),
            "②안정+현금미감당")
        self.assertEqual(
            div_group(False, None, True, 0.0, False), "③불안정배당")
        self.assertEqual(
            div_group(False, None, False, 50.0, False), "③불안정배당")
        self.assertEqual(
            div_group(False, None, False, 0.0, False), "④무배당")
        self.assertEqual(
            div_group(False, None, False, None, False), "④무배당")


class TestPayoutBucket(unittest.TestCase):
    def test_buckets(self):
        """성향 구간 5케이스."""
        self.assertEqual(payout_bucket(30, 100), "<50%")
        self.assertEqual(payout_bucket(80, 100), "50~100%")
        self.assertEqual(payout_bucket(150, 100), ">100%/적자")
        self.assertEqual(payout_bucket(50, -10), ">100%/적자")
        self.assertIsNone(payout_bucket(0, 100))


class TestDivAdd(unittest.TestCase):
    def test_values(self):
        """(2.52,100,1.0)→0.0001, 0.85→0.000085."""
        self.assertAlmostEqual(div_add(2.52, 100, 1.0), 0.0001)
        self.assertAlmostEqual(div_add(2.52, 100, 0.85), 0.000085)


if __name__ == "__main__":
    unittest.main()
