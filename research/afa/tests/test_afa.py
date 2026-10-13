"""AFA 합성 테스트 (DB 없음)."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.afa.fair import mispricing, ols_fair, ts_fair
from research.afa.items import bal_obs, select_items, snapshot, ttm_obs, winsorize_by_assets
from research.afa.run import AFA_ITEMS, _aggregate, afa_matrix, pick_shares
from research.ccc.panel import load_facts


def _facts(rows) -> pd.DataFrame:
    """합성 facts."""
    df = pd.DataFrame(rows, columns=["cik", "tag", "start", "end", "filed", "val"])
    df["start"] = pd.to_datetime(df["start"])
    df["end"] = pd.to_datetime(df["end"])
    df["filed"] = pd.to_datetime(df["filed"])
    return df


class TestSnapshotTiming(unittest.TestCase):
    def test_r1_future_filed_unused(self):
        """avail>t 값 미사용."""
        f = _facts([
            (1, "T", None, "2020-01-01", "2020-02-01", 100.0),
            (1, "T", None, "2020-03-01", "2020-05-01", 200.0),
        ])
        snap = snapshot(bal_obs(f), pd.DataFrame(columns=["cik", "tag", "end", "avail", "val"]),
                        pd.Timestamp("2020-04-01"), ["T"])
        self.assertEqual(float(snap.loc[1, "T"]), 100.0)

    def test_r1b_old_end_dropped(self):
        """t-200일 이전 end 버림."""
        f = _facts([(1, "T", None, "2019-05-01", "2019-06-01", 100.0)])
        snap = snapshot(bal_obs(f), pd.DataFrame(columns=["cik", "tag", "end", "avail", "val"]),
                        pd.Timestamp("2020-04-01"), ["T"])
        self.assertTrue(snap.empty or bool(snap["T"].isna().all()))


class TestTtm(unittest.TestCase):
    def _base(self):
        return [
            (1, "S", "2018-01-01", "2018-12-31", "2019-02-01", 100.0),
            (1, "S", "2019-01-01", "2019-09-30", "2019-10-30", 80.0),
            (1, "S", "2018-01-01", "2018-09-30", "2018-10-30", 70.0),
        ]

    def test_r2_reconstructed(self):
        """연간+당기YTD−전년YTD."""
        out = ttm_obs(_facts(self._base()))
        row = out[out["end"] == pd.Timestamp("2019-09-30")]
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(float(row["val"].iloc[0]), 110.0)
        self.assertEqual(pd.Timestamp(row["avail"].iloc[0]), pd.Timestamp("2019-10-30"))

    def test_r2_no_prior_no_row(self):
        """전년 YTD 없으면 재구성 없음."""
        out = ttm_obs(_facts(self._base()[:2]))
        self.assertTrue((out["end"] != pd.Timestamp("2019-09-30")).all())

    def test_r2_annual_direct(self):
        """연간 직접값 그대로."""
        out = ttm_obs(_facts(self._base()))
        row = out[out["end"] == pd.Timestamp("2018-12-31")]
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(float(row["val"].iloc[0]), 100.0)


class TestFair(unittest.TestCase):
    def test_r3_outlier_overvalued(self):
        """큰 양수 노이즈 기업 M 음수."""
        rng = np.random.default_rng(0)
        A = np.linspace(10.0, 100.0, 50)
        e = rng.normal(0.0, 1.0, 50)
        e[0] += 50.0
        v = 2 * A + e
        m = mispricing(ols_fair(A[:, None], v), v)
        self.assertLess(float(m[0]), 0.0)

    def test_r4_rank_deficient(self):
        """완전 공선 열 추가해도 예측값 동일."""
        rng = np.random.default_rng(0)
        A = rng.uniform(10.0, 100.0, 40)
        v = 2 * A + rng.normal(0.0, 1.0, 40)
        p1 = ols_fair(A[:, None], v)
        p2 = ols_fair(np.column_stack([A, A]), v)
        self.assertTrue(np.allclose(p1, p2))

    def test_r5_robust(self):
        """이상치에 TS 가 OLS 보다 덜 끌림."""
        rng = np.random.default_rng(1)
        A = rng.uniform(10.0, 100.0, 200)
        v = 2 * A + rng.normal(0.0, 0.5, 200)
        v[0] = 2 * A[0] * 100
        X = A[:, None]
        eo = float(np.mean(np.abs(ols_fair(X, v)[1:] - 2 * A[1:])))
        et = float(np.mean(np.abs(ts_fair(X, v, draws=500, m=30, seed=0)[1:] - 2 * A[1:])))
        self.assertLess(et, eo)

    def test_r6_residual_sum_zero(self):
        """상수항 OLS 잔차합 0."""
        rng = np.random.default_rng(2)
        X = rng.uniform(1.0, 10.0, (60, 3))
        v = X @ np.array([1.5, -2.0, 0.5]) + rng.normal(0.0, 1.0, 60)
        self.assertLess(abs(float(np.sum(ols_fair(X, v) - v))), 1e-6)

    def test_r9_ts_collinear(self):
        """완전 공선에도 NaN 없음, 단일열과 일치."""
        rng = np.random.default_rng(0)
        x = rng.uniform(10.0, 100.0, 200)
        v = 2 * x + rng.normal(0.0, 1.0, 200)
        p1 = ts_fair(x[:, None], v, draws=200, m=30, seed=0)
        p2 = ts_fair(np.column_stack([x, x]), v, draws=200, m=30, seed=0)
        self.assertFalse(bool(np.isnan(p2).any()))
        self.assertTrue(np.allclose(p1, p2, rtol=1e-3))


class TestItems(unittest.TestCase):
    def test_r7_greedy_cover(self):
        """커버리지 탐욕 선택."""
        idx = [f"C{i}" for i in range(10)]
        snap = pd.DataFrame({"A": [1.0] * 10,
                             "B": [1.0] * 8 + [np.nan] * 2,
                             "C": [1.0] * 7 + [np.nan] * 3,
                             "D": [1.0] * 5 + [np.nan] * 5}, index=pd.Index(idx, name="cik"))
        self.assertEqual(select_items(snap, idx, frac=0.6), ["A", "B", "C"])

    def test_r8_winsorize(self):
        """비율 clip, Assets 불변."""
        assets = pd.Series([10.0, 20.0, 30.0, 40.0, 100.0])
        X = pd.DataFrame({"Assets": assets,
                          "X1": np.array([1.0, 2.0, 3.0, 4.0, 100.0]) * assets.to_numpy()})
        out = winsorize_by_assets(X, assets)
        self.assertTrue(bool((out["Assets"] == X["Assets"]).all()))
        self.assertLess(float(out["X1"].iloc[4]), float(X["X1"].iloc[4]))
        self.assertAlmostEqual(float(out["X1"].iloc[4] / assets.iloc[4]), 80.8)


class TestLoadFactsBadDate(unittest.TestCase):
    def test_bad_start_dropped(self):
        """잘못된 start 날짜 행 제거."""
        with tempfile.TemporaryDirectory() as d:
            db = f"{d}/t.sqlite3"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE concepts (concept_id INTEGER PRIMARY KEY, taxonomy TEXT, tag TEXT)")
            con.execute("CREATE TABLE facts (cik INTEGER, concept_id INTEGER, unit TEXT, start TEXT, end TEXT, filed TEXT, val REAL)")
            con.executemany("INSERT INTO concepts VALUES (?, ?, ?)",
                            [(1, "us-gaap", "Assets"), (2, "us-gaap", "Revenues")])
            con.executemany("INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?)",
                            [(1, 1, "USD", "", "2020-12-31", "2021-02-01", 100.0),
                             (1, 2, "USD", "0202-02-01", "2020-12-31", "2021-02-01", 50.0)])
            con.commit()
            con.close()
            out = load_facts(db_path=db, extra_tags=("Revenues",))
        self.assertEqual(len(out), 1)
        self.assertEqual(out["tag"].iloc[0], "Assets")


class TestAfaMatrix(unittest.TestCase):
    def test_derived(self):
        """1행 std 표 → 파생 항목, income_before_xo 결측은 대체."""
        s = pd.DataFrame({
            "total_assets": [1000.0], "total_current_assets": [400.0],
            "cash_and_sti": [100.0], "accounts_receivable": [80.0],
            "inventory": [50.0], "property_plant_equipment": [300.0],
            "goodwill": [20.0], "intangible_assets": [10.0],
            "long_term_investments": [30.0],
            "total_current_liabilities": [200.0], "accounts_payable": [60.0],
            "short_term_debt": [40.0], "total_liabilities": [500.0],
            "long_term_debt": [200.0], "deferred_tax_liabilities": [15.0],
            "stockholders_equity": [400.0], "preferred_stock": [25.0],
            "minority_interest": [5.0], "income_before_xo": [np.nan],
            "net_income": [90.0], "discontinued_ops": [10.0],
            "preferred_dividends": [2.0],
        }, index=pd.Index([1], name="cik"))
        out = afa_matrix(s)
        self.assertEqual(list(out.columns), AFA_ITEMS)
        self.assertAlmostEqual(float(out.loc[1, "ACOQ"]), 170.0)
        self.assertAlmostEqual(float(out.loc[1, "AOQ"]), 240.0)
        self.assertAlmostEqual(float(out.loc[1, "LCOQ"]), 100.0)
        self.assertAlmostEqual(float(out.loc[1, "LOQ"]), 85.0)
        self.assertAlmostEqual(float(out.loc[1, "CEQQ"]), 375.0)
        self.assertAlmostEqual(float(out.loc[1, "IBQ"]), 80.0)
        self.assertAlmostEqual(float(out.loc[1, "IBCOMQ"]), 78.0)

    def test_ap_fallback(self):
        """accounts_payable 결측 → ap_and_accrued, LCOQ도 채운 값 사용."""
        s = pd.DataFrame({
            "total_current_liabilities": [200.0],
            "accounts_payable": [np.nan],
            "ap_and_accrued": [30.0],
            "short_term_debt": [40.0],
        }, index=pd.Index([1], name="cik"))
        out = afa_matrix(s)
        self.assertAlmostEqual(float(out.loc[1, "APQ"]), 30.0)
        self.assertAlmostEqual(float(out.loc[1, "LCOQ"]), 130.0)


class TestPickShares(unittest.TestCase):
    T = pd.Timestamp("2024-06-30")

    def _sh(self, rows) -> pd.DataFrame:
        """합성 주식수 (cik, end, filed, val)."""
        df = pd.DataFrame(rows, columns=["cik", "end", "filed", "val"])
        df["end"] = pd.to_datetime(df["end"])
        df["filed"] = pd.to_datetime(df["filed"])
        return df

    def test_median_and_ratio(self):
        """(a) wavg 탈락 2값 dei 우선 2.08억, (b) wavg 탈락 단일값 1.47억,
        (c) dei 탈락 단일값 1억, (d) 비율 내 dei 우선 1.1억."""
        t = self.T
        dei = self._sh([
            (1, "2024-03-31", "2024-05-01", 2.08e8),
            (2, "2024-03-31", "2024-05-01", 1.47e8),
            (3, "2024-03-31", "2024-05-01", 1000.0),
            (4, "2024-03-31", "2024-05-01", 1.1e8),
        ])
        wavg = self._sh([
            (1, "2024-03-31", "2024-05-01", 195630.0),
            (2, "2024-03-31", "2024-05-01", 150.4),
            (3, "2024-03-31", "2024-05-01", 1.0e8),
            (4, "2024-03-31", "2024-05-01", 1.0e8),
        ])
        cso = self._sh([
            (1, "2024-03-31", "2024-05-01", 1.95e8),
        ])
        out = pick_shares(dei, wavg, cso, t)
        # (a) wavg 195630은 MIN_SHARES 미만이라 NaN → 2값 비율 내 dei 우선.
        self.assertAlmostEqual(float(out.loc[1]), 2.08e8)
        # (b) wavg 150.4는 MIN_SHARES 미만이라 NaN → dei 단일값.
        self.assertAlmostEqual(float(out.loc[2]), 1.47e8)
        # (c) dei 1000은 MIN_SHARES 미만이라 NaN → wavg 단일값.
        self.assertAlmostEqual(float(out.loc[3]), 1.0e8)
        self.assertAlmostEqual(float(out.loc[4]), 1.1e8)

    def test_no_wavg_excluded(self):
        """(e) wavg 없음 → 결과에 없음."""
        t = self.T
        dei = self._sh([(5, "2024-03-31", "2024-05-01", 5.0e7)])
        wavg = self._sh([(6, "2024-03-31", "2024-05-01", 5.0e7)])
        cso = self._sh([(5, "2024-03-31", "2024-05-01", 5.1e7)])
        out = pick_shares(dei, wavg, cso, t)
        self.assertNotIn(5, out.index)
        self.assertIn(6, out.index)

    def test_stale_and_future_unused(self):
        """(f) 400일 넘은 end·filed>t 값은 안 쓰임."""
        t = self.T
        dei = self._sh([
            (7, "2024-03-31", "2024-05-01", 5.0e7),
            (8, "2024-03-31", t + pd.Timedelta(days=10), 9.0e7),
            (9, t - pd.Timedelta(days=500), t - pd.Timedelta(days=480), 9.0e7),
        ])
        wavg = self._sh([
            (7, t - pd.Timedelta(days=500), t - pd.Timedelta(days=480), 5.0e7),
            (8, "2024-03-31", "2024-05-01", 5.0e7),
            (9, "2024-03-31", "2024-05-01", 5.0e7),
            (10, "2024-03-31", t + pd.Timedelta(days=10), 5.0e7),
        ])
        cso = self._sh([])
        out = pick_shares(dei, wavg, cso, t)
        self.assertNotIn(7, out.index)
        self.assertAlmostEqual(float(out.loc[8]), 5.0e7)
        self.assertAlmostEqual(float(out.loc[9]), 5.0e7)
        self.assertNotIn(10, out.index)

    def test_small_wavg_only_excluded(self):
        """(g) wavg 60623만 있음 → 결과에 없음."""
        t = self.T
        dei = self._sh([])
        wavg = self._sh([(11, "2024-03-31", "2024-05-01", 60623.0)])
        cso = self._sh([])
        out = pick_shares(dei, wavg, cso, t)
        self.assertNotIn(11, out.index)

    def test_small_wavg_with_valid_cso(self):
        """(h) wavg 60623 탈락 + cso 6천만 → 6천만."""
        t = self.T
        dei = self._sh([])
        wavg = self._sh([(12, "2024-03-31", "2024-05-01", 60623.0)])
        cso = self._sh([(12, "2024-03-31", "2024-05-01", 6.0e7)])
        out = pick_shares(dei, wavg, cso, t)
        self.assertAlmostEqual(float(out.loc[12]), 6.0e7)


class TestAggregateNoTs(unittest.TestCase):
    def test_no_ts_rows_when_m_ts_nan(self):
        """m_ts 전부 NaN이면 signal==ts 행 없음."""
        cs = pd.DataFrame({
            "ym": [202401] * 5,
            "cik": [1, 2, 3, 4, 5],
            "ticker": ["A", "B", "C", "D", "E"],
            "ind": [1, 1, 1, 1, 1],
            "ret": [0.01, 0.02, 0.03, 0.04, 0.05],
            "ret_ia": [0.01, 0.02, 0.03, 0.04, 0.05],
            "me": [1e9] * 5,
            "microcap": [False] * 5,
            "m_ols": [0.1, 0.2, 0.3, 0.4, 0.5],
            "m_ts": [float("nan")] * 5,
            "q_ols": [1, 2, 3, 4, 5],
            "q_ts": [0] * 5,
            "K": [28] * 5,
            "N": [5] * 5,
        })
        rows = _aggregate(cs, pd.DataFrame())
        self.assertTrue(rows)
        self.assertFalse(any(r["signal"] == "ts" for r in rows))
        self.assertTrue(any(r["signal"] == "ols" for r in rows))


if __name__ == "__main__":
    unittest.main()
