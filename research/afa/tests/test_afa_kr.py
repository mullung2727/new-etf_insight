"""AFA 한국 검증 K1~K7 (unittest, 합성 DataFrame·tempfile sqlite, DB 안 씀)."""

import os
import shutil
import sqlite3
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.afa.kr import (_aggregate_kr, _turnover, fin_corps, kr_snapshot,
                             load_kr_fin, monthly_frame)
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


if __name__ == "__main__":
    unittest.main()
