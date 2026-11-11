"""signal.py 단위 테스트 — 합성 60분봉만 사용(DB 접근 금지)."""
import unittest
from datetime import date, timedelta

import pandas as pd

from research.rsi_double_bottom.signal import (
    add_indicators, find_entries, find_naive,
)


def _frame(rows, ticker="000000"):
    recs = []
    d0 = date(2025, 12, 1)
    for k, (o, h, low, c) in enumerate(rows):
        d = (d0 + timedelta(days=k // 7)).strftime("%Y%m%d")
        hb = 9 + (k % 7)
        recs.append({"ticker": ticker, "date": d, "hb": hb, "open": o,
                     "high": h, "low": low, "close": c, "volume": 1000,
                     "last_time": f"{hb:02d}0000"})
    return pd.DataFrame(recs)


FLAT = [(100.0, 100.0, 100.0, 100.0)] * 30
DECLINE = [(100.0, 100.0, 97.0, 97.0),   # idx30
           (97.0, 97.0, 94.0, 94.0),     # idx31
           (94.0, 94.0, 91.0, 91.0),     # idx32: WATCH 진입
           (91.0, 91.0, 88.0, 88.0)]     # idx33: 연장

A_TAIL = [(87.5, 92.5, 87.0, 92.0),      # idx34 rev1
          (92.0, 92.3, 91.2, 91.6),      # idx35
          (91.6, 91.8, 90.8, 91.0),      # idx36
          (91.0, 91.2, 90.5, 90.7),      # idx37 두 번째 바닥
          (90.6, 93.0, 90.6, 92.5)]      # idx38 rev2 → 진입 (j=37=i-1)
A_FLAT = [(92.5, 92.5, 92.5, 92.5)] * 10

B_TAIL = [(87.5, 92.5, 87.0, 92.0),      # idx34 rev1 (첫 시도)
          (92.0, 92.2, 86.0, 87.0),      # idx35 X: 밴드 밖 → 재시작
          (86.8, 93.0, 86.5, 92.8),      # idx36 rev1'
          (92.8, 93.0, 91.5, 92.0),      # idx37'
          (92.0, 92.2, 91.2, 91.5),      # idx38' 두 번째 바닥
          (91.4, 93.5, 91.2, 93.5)]      # idx39' rev2' → 진입
B_FLAT = [(92.9, 92.9, 92.9, 92.9)] * 10

C_TAIL = [(89.0, 89.0, 89.0, 89.0)] * 25  # idx34..58 장악형 없음

D_TAIL = [(87.5, 92.5, 87.0, 92.0),      # idx34 rev1
          (92.0, 92.3, 91.2, 91.6),      # idx35 두 번째 바닥
          (91.6, 91.8, 91.4, 91.7),      # idx36
          (91.7, 91.8, 91.5, 91.6),      # idx37 음봉
          (91.3, 93.5, 91.3, 93.0),      # idx38 장악형이나 j=35 → 진입 아님
          (93.0, 93.2, 90.9, 91.0),      # idx39 새 바닥
          (90.9, 94.0, 90.9, 93.2)]      # idx40 바로 다음 장악형 → 진입
D_FLAT = [(93.2, 93.2, 93.2, 93.2)] * 10

E_TAIL = [(87.5, 92.5, 87.0, 92.0),      # idx34 rev1
          (92.0, 92.3, 91.2, 91.6),      # idx35
          (91.0, 93.5, 91.0, 93.0)]      # idx36 장악형이 구간 최저 → 진입
E_FLAT = [(93.0, 93.0, 93.0, 93.0)] * 10


PRE = [(10.0, 10.05, 9.95, 10.0)] * 20  # 몸통 0 도지 20봉


class TestEngulf(unittest.TestCase):
    def test_bull_engulf_cover(self):
        rows = PRE + [(10.0, 10.1, 8.9, 9.0),
                      (8.9, 10.2, 8.8, 10.1)]
        df = add_indicators(_frame(rows))
        self.assertTrue(df["bull_engulf"].iloc[21])

    def test_bull_engulf_no_cover(self):
        rows = PRE + [(10.0, 10.1, 8.9, 9.0),
                      (8.9, 9.9, 8.8, 9.8)]
        df = add_indicators(_frame(rows))
        self.assertFalse(df["bull_engulf"].iloc[21])

    def test_bear_engulf_cover(self):
        rows = PRE + [(9.0, 10.1, 8.9, 10.0),
                      (10.1, 10.2, 8.8, 8.9)]
        df = add_indicators(_frame(rows))
        self.assertTrue(df["bear_engulf"].iloc[21])

    def test_bear_engulf_no_cover(self):
        rows = PRE + [(9.0, 10.1, 8.9, 10.0),
                      (9.5, 9.6, 8.8, 8.9)]
        df = add_indicators(_frame(rows))
        self.assertFalse(df["bear_engulf"].iloc[21])

    def test_bull_engulf_small_body_false(self):
        pre = [(10.0, 12.0, 10.0, 12.0)] * 20
        rows = pre + [(12.0, 12.0, 11.0, 11.0),
                      (10.9, 12.1, 10.9, 12.0)]
        df = add_indicators(_frame(rows))
        # 덮기는 하지만 몸통이 직전 20봉 평균 이하
        self.assertLessEqual(df["body"].iloc[21], df["body_avg20"].iloc[21])
        self.assertFalse(df["bull_engulf"].iloc[21])


class TestScenarioA(unittest.TestCase):
    def test_full_pattern_one_entry(self):
        df = add_indicators(_frame(FLAT + DECLINE + A_TAIL + A_FLAT))
        # WATCH 진입 조건이 실제로 탔는지 지표로 확인
        self.assertLess(df["rsi14"].iloc[32], 30)
        self.assertTrue(df["three_bear"].iloc[32])
        self.assertLess(df["close"].iloc[32], df["bb_lo"].iloc[32])
        self.assertFalse(df["bull_engulf"].iloc[30:32].any())
        # 첫 상승장악 → 밴드 안 눌림 → 상승장악
        self.assertTrue(df["bull_engulf"].iloc[34])
        for i in (35, 36, 37):
            self.assertGreaterEqual(df["low"].iloc[i], df["bb_lo"].iloc[i])
            self.assertFalse(df["bull_engulf"].iloc[i])
        self.assertTrue(df["bull_engulf"].iloc[38])
        sigs = find_entries(df)
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual(s["sl"], 90.5)
        self.assertEqual(s["first_low"], 87.0)
        self.assertEqual(s["sig_date"], df["date"].iloc[38])
        self.assertEqual(s["sig_hb"], df["hb"].iloc[38])
        self.assertEqual(s["bottom2_date"], df["date"].iloc[37])
        self.assertEqual(s["bottom2_hb"], df["hb"].iloc[37])
        self.assertEqual(s["setup_date"], df["date"].iloc[34])

    def test_no_lookahead(self):
        df = add_indicators(_frame(FLAT + DECLINE + A_TAIL + A_FLAT))
        s0 = find_entries(df)[0]
        wild = ([50.0, 150.0, 50.0, 150.0, 50.0, 150.0, 50.0, 150.0,
                 50.0, 150.0])
        tail = [(c, c, c, c) for c in wild]
        df2 = add_indicators(_frame(FLAT + DECLINE + A_TAIL + tail))
        s1 = find_entries(df2)[0]
        self.assertEqual(s0, s1)


class TestScenarioB(unittest.TestCase):
    def test_band_break_restarts(self):
        df = add_indicators(_frame(FLAT + DECLINE + B_TAIL + B_FLAT))
        # X 봉이 밴드 밖인지 지표로 확인
        self.assertLess(df["low"].iloc[35], df["bb_lo"].iloc[35])
        self.assertTrue(df["bull_engulf"].iloc[34])
        self.assertTrue(df["bull_engulf"].iloc[36])
        for i in (37, 38):
            self.assertGreaterEqual(df["low"].iloc[i], df["bb_lo"].iloc[i])
        self.assertTrue(df["bull_engulf"].iloc[39])
        sigs = find_entries(df)
        # 재시작 전 시퀀스는 진입 없음, 재시작 후 유효 시퀀스만 진입 1건
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual(s["sl"], 91.2)
        self.assertEqual(s["sig_date"], df["date"].iloc[39])


class TestScenarioC(unittest.TestCase):
    def test_expiry_no_entry(self):
        df = add_indicators(_frame(FLAT + DECLINE + C_TAIL))
        # WATCH 진입은 됐는지 지표로 확인
        self.assertLess(df["rsi14"].iloc[32], 30)
        self.assertTrue(df["three_bear"].iloc[32])
        self.assertLess(df["close"].iloc[32], df["bb_lo"].iloc[32])
        # 꼬리 25봉에 장악형 없음 확인
        self.assertFalse(df["bull_engulf"].iloc[34:].any())
        self.assertEqual(find_entries(df), [])


class TestLateEngulf(unittest.TestCase):
    def test_late_engulf_skipped_then_new_bottom_entry(self):
        df = add_indicators(_frame(FLAT + DECLINE + D_TAIL + D_FLAT))
        for i in (34, 38, 40):
            self.assertTrue(df["bull_engulf"].iloc[i])
        for i in (35, 36, 37, 39):
            self.assertFalse(df["bull_engulf"].iloc[i])
        for i in (35, 36, 37, 38, 39, 40):
            self.assertGreaterEqual(df["low"].iloc[i], df["bb_lo"].iloc[i])
        sigs = find_entries(df)
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual(s["sig_date"], df["date"].iloc[40])
        self.assertEqual(s["sig_hb"], df["hb"].iloc[40])
        self.assertEqual(s["sl"], 90.9)
        self.assertEqual(s["bottom2_date"], df["date"].iloc[40])
        self.assertEqual(s["bottom2_hb"], df["hb"].iloc[40])


class TestEntryBarIsBottom(unittest.TestCase):
    def test_engulf_bar_is_range_low(self):
        df = add_indicators(_frame(FLAT + DECLINE + E_TAIL + E_FLAT))
        self.assertTrue(df["bull_engulf"].iloc[34])
        self.assertFalse(df["bull_engulf"].iloc[35])
        self.assertTrue(df["bull_engulf"].iloc[36])
        for i in (35, 36):
            self.assertGreaterEqual(df["low"].iloc[i], df["bb_lo"].iloc[i])
        sigs = find_entries(df)
        self.assertEqual(len(sigs), 1)
        s = sigs[0]
        self.assertEqual(s["sl"], 91.0)
        self.assertEqual(s["bottom2_date"], df["date"].iloc[36])
        self.assertEqual(s["bottom2_hb"], df["hb"].iloc[36])


class TestNaive(unittest.TestCase):
    def test_crossing_signal(self):
        closes = [100.0 + (k % 2) for k in range(40)]
        rows = [(closes[0], closes[0], closes[0], closes[0])]
        prev = closes[0]
        for c in closes[1:]:
            rows.append((prev, max(prev, c), min(prev, c), c))
            prev = c
        for _ in range(10):
            nxt = prev * 0.97
            rows.append((prev, prev, nxt, nxt))
            prev = nxt
        df = add_indicators(_frame(rows))
        rsi = df["rsi14"]
        expect = [i for i in range(1, len(df))
                  if rsi.iloc[i] < 30 <= rsi.iloc[i - 1]]
        self.assertGreaterEqual(len(expect), 1)
        sigs = find_naive(df)
        self.assertEqual(len(sigs), len(expect))
        for s, i in zip(sigs, expect):
            self.assertEqual(s["sig_date"], df["date"].iloc[i])
            self.assertEqual(s["sig_hb"], df["hb"].iloc[i])
            self.assertEqual(s["sl"], df["low"].iloc[i])


if __name__ == "__main__":
    unittest.main()
