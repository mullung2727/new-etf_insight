"""AFA 한국 검증 K1~K7 (unittest, 합성 DataFrame·tempfile sqlite, DB 안 씀)."""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research.afa.kr import (CS_COLS, ITEMS, MIN_N, _aggregate_kr, _quintile,
                             _turnover, fin_corps, form_month, formation_rows,
                             kr_snapshot, load_kr_fin, monthly_frame,
                             universe_mask)
from research.afa.fair import mispricing, ols_fair
from research.afa.items import winsorize_by_assets
from research.backtest_daily.stats import COSTS

SCHEMA = ("CREATE TABLE accounts (corp_code TEXT, bsns_year TEXT, reprt_code TEXT, "
          "fs_div TEXT, sj_div TEXT, account_nm TEXT, amount REAL, stock_code TEXT)")


def make_db(rows):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "fin.sqlite3")
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    con.executemany(
        "INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return tmp, path


def R(code, year, rc, fs, nm, amt, ticker="000001"):
    return (code, year, rc, fs, "BS" if nm == "자산총계" else "IS", nm, amt, ticker)


class KrFinTest(unittest.TestCase):
    def tearDown(self):
        if hasattr(self, "_tmp"):
            shutil.rmtree(self._tmp, ignore_errors=True)

    def _load(self, rows):
        self._tmp, path = make_db(rows)
        return load_kr_fin(db=path)

    def test_K1_q4_backout_and_ttm(self):
        rows = [R("C1", "2023", "11013", "OFS", "매출액", 20),
                R("C1", "2023", "11012", "OFS", "매출액", 25),
                R("C1", "2023", "11014", "OFS", "매출액", 30),
                R("C1", "2023", "11011", "OFS", "매출액", 100)]
        fin = self._load(rows)
        q4 = fin[(fin["stock_code"] == "000001") & (fin["item"] == "매출액")
                 & (fin["qend"] == pd.Timestamp("2023-12-31"))]
        self.assertEqual(len(q4), 1)
        self.assertAlmostEqual(float(q4["val"].iloc[0]), 25.0)  # 100-20-25-30
        snap = kr_snapshot(fin, "2024-04-30")
        self.assertAlmostEqual(float(snap.loc["000001", "매출액"]), 100.0)

    def test_K2_missing_quarter_nan(self):
        rows = [R("C1", "2023", "11013", "OFS", "매출액", 20),
                R("C1", "2023", "11014", "OFS", "매출액", 30),
                R("C1", "2023", "11011", "OFS", "매출액", 100)]
        fin = self._load(rows)
        snap = kr_snapshot(fin, "2024-04-30")
        self.assertTrue("000001" not in snap.index or pd.isna(snap.loc["000001", "매출액"]))

    def test_K3_annual_avail(self):
        rows = [R("C1", "2022", "11011", "OFS", "자산총계", 1000),
                R("C1", "2023", "11011", "OFS", "자산총계", 2000)]
        fin = self._load(rows)
        before = kr_snapshot(fin, "2024-03-29")
        after = kr_snapshot(fin, "2024-04-30")
        if "000001" in before.index:
            v = before.loc["000001", "자산총계"]
            self.assertTrue(pd.isna(v) or float(v) == 1000.0)
        self.assertAlmostEqual(float(after.loc["000001", "자산총계"]), 2000.0)

    def test_K4_cfs_preferred(self):
        rows = [R("C1", "2023", "11013", "CFS", "자산총계", 500),
                R("C1", "2023", "11013", "CFS", "매출액", 50),
                R("C1", "2023", "11013", "OFS", "자산총계", 700),
                R("C1", "2023", "11013", "OFS", "매출액", 70),
                R("C2", "2023", "11013", "OFS", "자산총계", 900, ticker="000002"),
                R("C2", "2023", "11013", "OFS", "매출액", 90, ticker="000002")]
        fin = self._load(rows)
        c1 = fin[(fin["stock_code"] == "000001") & (fin["item"] == "매출액")]
        self.assertEqual(len(c1), 1)
        self.assertAlmostEqual(float(c1["val"].iloc[0]), 50.0)  # CFS 사용, OFS 무시
        c2 = fin[(fin["stock_code"] == "000002") & (fin["item"] == "매출액")]
        self.assertEqual(len(c2), 1)
        self.assertAlmostEqual(float(c2["val"].iloc[0]), 90.0)  # CFS 없으면 OFS

    def test_K5_fin_corps(self):
        rows = [R("C1", "2023", "11011", "OFS", "자산총계", 1000),
                R("C9", "2023", "11011", "OFS", "예수부채", 5, ticker="000009")]
        self._tmp, path = make_db(rows)
        corps = fin_corps(db=path)
        self.assertIn("000009", corps)
        self.assertNotIn("000001", corps)


def synth_px():
    # 형성일 20240131, 다음 형성일 20240229. 형성일 당일 +10%는 제외돼야 함.
    close = 100.0

    def cp(r):
        return close * r / (1 + r)

    rows = [
        ("20240130", "000001", 0.0),    # 형성 전일 (무시)
        ("20240131", "000001", 0.10),   # 형성일 당일 (제외)
        ("20240201", "000001", 0.01),   # 보유 1
        ("20240229", "000001", 0.02),   # 보유 2
        ("20240131", "000002", 0.05),   # 형성일에만 (상폐 가정) -> ret 0
    ]
    ms = {"20240130": 0, "20240131": 1, "20240201": 2, "20240229": 3}
    out = []
    for d, tk, r in rows:
        out.append({"date": d, "ticker": tk, "market": "KOSPI", "open": close,
                    "high": close, "low": close, "close": close, "volume": 100,
                    "trading_value": 1e7, "market_cap": 1e10,
                    "cmp_prev": cp(r), "ms": ms[d]})
    return pd.DataFrame(out)


class MonthlyFrameTest(unittest.TestCase):
    def test_K6_excludes_formation_day(self):
        fr = monthly_frame(synth_px())
        self.assertIn("tv", fr.columns)
        a = fr[(fr["ym"] == 202401) & (fr["ticker"] == "000001")]
        self.assertEqual(len(a), 1)
        self.assertAlmostEqual(float(a["tv"].iloc[0]), 1e7)
        self.assertEqual(int(a["ret_ym"].iloc[0]), 202402)
        self.assertAlmostEqual(float(a["ret"].iloc[0]), 1.01 * 1.02 - 1, places=9)
        b = fr[(fr["ym"] == 202401) & (fr["ticker"] == "000002")]
        self.assertEqual(len(b), 1)
        self.assertAlmostEqual(float(b["ret"].iloc[0]), 0.0)  # 다음 달 행 없음


class TurnoverTest(unittest.TestCase):
    def test_K7_same_members_zero(self):
        s = {"a", "b", "c"}
        self.assertAlmostEqual(_turnover(s, set(s)), 0.0)
        ret = 0.05
        self.assertAlmostEqual(ret - _turnover(s, set(s)) * COSTS[1], ret)

    def test_K7_aggregate_net_equals_gross(self):
        # 20종목 × 2수익월, 극단 10 멤버 고정 -> 회전율 0, 비용후 = 비용전.
        tickers = [f"T{i:02d}" for i in range(20)]
        rows = []
        for ym, rym in ((202312, 202401), (202401, 202402)):
            for i, tk in enumerate(tickers):
                rows.append({"ym": ym, "ret_ym": rym, "ticker": tk, "me": 1e10,
                             "ret": 0.01 * ((i % 5) + 1), "m": float(i - 10),
                             "q": min(i // 4 + 1, 5), "N": 20})
        agg = _aggregate_kr(pd.DataFrame(rows))["ALL"]
        for key in ("ext_lo", "ext_hi"):
            self.assertAlmostEqual(agg[key]["to"], 0.0)
            self.assertAlmostEqual(agg[key]["net"], agg[key]["mean"])


def form_inputs(n=MIN_N):
    tickers = [f"{i:06d}" for i in range(1000, 1000 + n)]
    g = pd.DataFrame({"ticker": tickers, "me": np.linspace(1e9, 2e9, n),
                      "ym": 202401, "ret_ym": 202402,
                      "ret": np.linspace(-0.1, 0.1, n)})
    snap = pd.DataFrame({c: np.linspace(100 + j, 1000 + j, n)
                         for j, c in enumerate(ITEMS)}, index=tickers)
    snap["자산총계"] = np.linspace(1e8, 1e9, n)
    return g, snap


class FormationFunctionsTest(unittest.TestCase):
    def test_K8_form_month_matches_loop(self):
        g, snap = form_inputs()
        t = pd.Timestamp("2024-01-31")
        with patch("research.afa.kr.kr_snapshot", return_value=snap):
            actual, count = form_month(g, None, t)
        sub = snap.loc[snap.index.intersection(g["ticker"].astype(str).unique())]
        sub = sub.dropna(subset=ITEMS)
        sub = sub[sub["자산총계"] > 0]
        X = winsorize_by_assets(sub[ITEMS], sub["자산총계"]).to_numpy(float)
        gi = g.drop_duplicates("ticker", keep="last").set_index("ticker")
        v = gi.reindex(sub.index)["me"].to_numpy(float)
        okv = np.isfinite(v) & (v > 0)
        sub, X, v = sub[okv], X[okv], v[okv]
        m = mispricing(ols_fair(X, v), v)
        q = _quintile(pd.Series(m, index=sub.index)).to_numpy(int)
        expected = pd.DataFrame({"ym": 202401,
                                 "ret_ym": gi.reindex(sub.index)["ret_ym"].to_numpy(int),
                                 "ticker": sub.index.astype(str).to_numpy(),
                                 "me": v,
                                 "ret": gi.reindex(sub.index)["ret"].to_numpy(float),
                                 "m": m, "q": q, "N": int(len(sub))})[CS_COLS]
        self.assertEqual(count, len(sub))
        pd.testing.assert_frame_equal(actual, expected)

    def test_K9_form_month_without_ret(self):
        g, snap = form_inputs()
        g = g.drop(columns=["ym", "ret_ym", "ret"])
        t = pd.Timestamp("2024-01-31")
        with patch("research.afa.kr.kr_snapshot", return_value=snap):
            actual, _ = form_month(g, None, t)
            with_ret, _ = form_month(
                form_inputs()[0], None, t)
        self.assertTrue(actual["ret"].isna().all())
        self.assertTrue((actual["ret_ym"] == -1).all())
        self.assertTrue((actual["ym"] == 202401).all())
        np.testing.assert_allclose(actual["m"], with_ret["m"])

    def test_K10_form_month_min_n_skip(self):
        g, snap = form_inputs(MIN_N - 1)
        with patch("research.afa.kr.kr_snapshot", return_value=snap):
            result, count = form_month(g, None, pd.Timestamp("2024-01-31"))
        self.assertIsNone(result)
        self.assertEqual((result, count), (None, MIN_N - 1))

    def test_K11_universe_mask(self):
        f = pd.DataFrame({"ticker": ["000010", "000011", "000020", "000030",
                                       "000040", "000050"],
                          "market": ["KOSPI", "KOSPI", "KOSPI", "KOSPI",
                                     "KOSPI", "KONEX"],
                          "me": [2e9, 2e9, 2e9, 2e9, 0, 2e9],
                          "tv": [2e7] * 6, "close": [2000] * 6})
        names = {"000020": "테스트스팩"}
        normal = universe_mask(f, names, {"000030"})
        self.assertEqual(list(f.loc[normal, "ticker"]), ["000010"])
        old = universe_mask(f, names, set(), old_filter=True)
        self.assertEqual(list(f.loc[old, "ticker"]), ["000010", "000030"])

    def test_K12_formation_rows(self):
        px = pd.DataFrame([
            {"date": "20240131", "ticker": "000001", "close": 10,
             "market_cap": 100, "market": "KOSPI", "trading_value": 20},
            {"date": "20240131", "ticker": "000001", "close": 11,
             "market_cap": 110, "market": "KOSDAQ", "trading_value": 21},
            {"date": "20240201", "ticker": "000002", "close": 12,
             "market_cap": 120, "market": "KOSPI", "trading_value": 22},
        ])
        rows = formation_rows(px, "20240131")
        self.assertEqual(list(rows.columns), ["ticker", "close", "me", "market", "tv", "ym"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0].to_dict(),
                         {"ticker": "000001", "close": 11.0, "me": 110.0,
                          "market": "KOSDAQ", "tv": 21.0, "ym": 202401})


if __name__ == "__main__":
    unittest.main()
