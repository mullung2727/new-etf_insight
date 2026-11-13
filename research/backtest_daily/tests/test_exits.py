"""exits 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import pandas as pd

from research.backtest_daily.exits import bracket_exit, hold_exit, trailing_exit


def _df(ms, P=None):
    ms = list(ms)
    n = len(ms)
    if P is None:
        P = [1.0] * n
    return pd.DataFrame({"ms": ms, "P": list(P)})


class TestHoldExit(unittest.TestCase):
    def test_cut_uses_last_frozen(self):
        df = _df([0, 1, 2, 5, 6], P=[1.0, 2.0, 3.0, 4.0, 5.0])
        pos, frozen, reason = hold_exit(df, 0, 3, 10)
        self.assertEqual(pos, 2)
        self.assertEqual(df["P"].iloc[pos], 3.0)
        self.assertTrue(frozen)
        self.assertIsNone(reason)

    def test_exact_and_open(self):
        df = _df([0, 1, 2, 5, 6], P=[1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(hold_exit(df, 0, 2, 10), (2, False, None))
        self.assertEqual(hold_exit(df, 3, 10, 10), (None, False, "open"))


class TestTrailingExit(unittest.TestCase):
    def test_trail_trigger(self):
        # 진입 100 → 150(+50%) 후 119 < 150*0.8=120 → 2행 청산
        pos, frozen, reason = trailing_exit([0, 1, 2, 3], [100.0, 150.0, 119.0, 130.0],
                                            0, 100.0, ms_max=1000)
        self.assertEqual((pos, frozen, reason), (2, False, None))

    def test_entry_day_not_judged(self):
        # 진입 당일 종가 -25%여도 당일 청산 없음 → max_hold행
        P = [75.0] + [90.0] * 299
        pos, frozen, reason = trailing_exit(list(range(300)), P, 0, 100.0, ms_max=1000)
        self.assertEqual((pos, frozen, reason), (250, False, None))
        # 다음 행부터 판정: 79 < 100*0.8 → 1행 청산
        P2 = [75.0, 79.0] + [200.0] * 298
        pos, _, _ = trailing_exit(list(range(300)), P2, 0, 100.0, ms_max=1000)
        self.assertEqual(pos, 1)

    def test_frozen_on_cut(self):
        # 목표ms 250 ≤ ms_max지만 행이 ms9에서 끊김 → 마지막행 고정
        pos, frozen, reason = trailing_exit(list(range(10)), [100.0] * 10, 0, 100.0,
                                            ms_max=300)
        self.assertEqual((pos, frozen, reason), (9, True, None))

    def test_open_beyond_db(self):
        pos, frozen, reason = trailing_exit(list(range(10)), [100.0] * 10, 0, 100.0,
                                            ms_max=200)
        self.assertEqual((pos, frozen, reason), (None, False, "open"))

    def test_nan_skipped_not_stuck(self):
        # 고점 150 → NaN → 112.5(고점 대비 -25%) → 3행 청산, NaN행 청산 없음
        P = [100.0, 150.0, float("nan"), 112.5]
        pos, frozen, reason = trailing_exit([0, 1, 2, 3], P, 0, 100.0, ms_max=1000)
        self.assertEqual((pos, frozen, reason), (3, False, None))


class TestBracketExit(unittest.TestCase):
    def _run(self, ms, O, H, C, entry_pos=0, p_entry=100.0, stop_px=90.0,
             tp_px=110.0, **kw):
        L = [c - 1.0 for c in C]
        return bracket_exit(ms, O, H, L, C, entry_pos, p_entry, stop_px,
                            tp_px, **kw)

    def _flat(self, n, c=100.0):
        return list(range(n)), [c] * n, [c + 2.0] * n, [c] * n

    def test_tp_intraday(self):
        ms = [0, 1, 2]
        O = [100.0, 105.0, 105.0]
        H = [102.0, 115.0, 106.0]
        C = [101.0, 108.0, 107.0]
        self.assertEqual(self._run(ms, O, H, C), (1, 110.0, "tp"))

    def test_tp_touch_not_filled(self):
        # H == tp 면 미체결 → 끝까지 가서 last
        ms = [0, 1, 2]
        O = [100.0, 105.0, 105.0]
        H = [102.0, 110.0, 106.0]
        C = [101.0, 108.0, 107.0]
        self.assertEqual(self._run(ms, O, H, C), (2, 107.0, "last"))

    def test_tp_gap(self):
        ms = [0, 1]
        O = [100.0, 112.0]
        H = [102.0, 113.0]
        C = [101.0, 111.0]
        self.assertEqual(self._run(ms, O, H, C), (1, 112.0, "tp_gap"))

    def test_entry_row_no_gap(self):
        # 진입 행 O >= tp 여도 갭 판정 없음 → H == tp 미체결 → last
        ms = [0, 1]
        O = [110.0, 105.0]
        H = [110.0, 106.0]
        C = [105.0, 107.0]
        self.assertEqual(self._run(ms, O, H, C), (1, 107.0, "last"))

    def test_entry_row_intraday_tp(self):
        # 진입 행도 H > tp 면 장중 체결
        ms = [0, 1]
        O = [100.0, 105.0]
        H = [115.0, 106.0]
        C = [108.0, 107.0]
        self.assertEqual(self._run(ms, O, H, C), (0, 110.0, "tp"))

    def test_sl_close(self):
        ms = [0, 1]
        O = [100.0, 95.0]
        H = [102.0, 96.0]
        C = [100.0, 85.0]
        self.assertEqual(self._run(ms, O, H, C), (1, 85.0, "sl"))

    def test_tp_before_sl_same_row(self):
        ms = [0, 1]
        O = [100.0, 105.0]
        H = [102.0, 115.0]
        C = [100.0, 85.0]
        self.assertEqual(self._run(ms, O, H, C), (1, 110.0, "tp"))

    def test_sl_limit_down(self):
        # 70/100-1 = -30% 하한가 → 다음 행 시가
        ms = [0, 1, 2]
        O = [100.0, 95.0, 68.0]
        H = [102.0, 96.0, 70.0]
        C = [100.0, 70.0, 65.0]
        self.assertEqual(self._run(ms, O, H, C),
                         (2, 68.0, "sl_limit_down"))

    def test_sl_limit_down_needs_continuous_ms(self):
        # ms 건너뜀이면 하한가여도 당일 종가 청산
        ms = [0, 1, 5]
        O = [100.0, 95.0, 60.0]
        H = [102.0, 96.0, 61.0]
        C = [100.0, 95.0, 60.0]
        self.assertEqual(self._run(ms, O, H, C), (2, 60.0, "sl"))

    def test_sl_limit_down_last_row_open(self):
        # 마지막 행 하한가 + ms가 ms_max 도달 → 내일 시가 미확정 open
        ms = [0, 1]
        O = [100.0, 70.0]
        H = [100.0, 70.0]
        C = [100.0, 70.0]
        self.assertEqual(
            self._run(ms, O, H, C, stop_px=90.0, tp_px=float("inf"),
                      max_hold=1, ms_max=1),
            (None, None, "open"))

    def test_sl_limit_down_last_row_delisted(self):
        # 마지막 행 하한가 + ms_max 전 종료(상폐) → last
        ms = [0, 1]
        O = [100.0, 70.0]
        H = [100.0, 70.0]
        C = [100.0, 70.0]
        self.assertEqual(
            self._run(ms, O, H, C, stop_px=90.0, tp_px=float("inf"),
                      max_hold=1, ms_max=5),
            (1, 70.0, "last"))

    def test_time_rule_exit(self):
        ms, O, H, C = self._flat(30)
        C[26] = 95.0
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=1000.0, time_bars=26,
                      ms_max=1000),
            (26, 95.0, "time"))

    def test_time_rule_continue_then_max(self):
        ms, O, H, C = self._flat(40)
        C[26] = 105.0
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=1000.0, time_bars=26,
                      max_hold=30, ms_max=1000),
            (30, 100.0, "max"))

    def test_time_row_with_gap(self):
        # ms 26 결측 → 처음 넘는 행(ms27, idx26)의 직전 행(idx25)에서 판정
        ms = list(range(26)) + list(range(27, 40))
        n = len(ms)
        O = [100.0] * n
        H = [102.0] * n
        C = [100.0] * n
        C[25] = 95.0
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=1000.0, time_bars=26,
                      ms_max=1000),
            (25, 95.0, "time"))

    def test_time_row_beyond_data_open(self):
        # 행이 tb 전에 끝남(j==n) → 시간 청산 발동 금지 → open
        ms, O, H, C = self._flat(10)
        C[-1] = 95.0
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=float("inf"), time_bars=26,
                      ms_max=50),
            (None, None, "open"))

    def test_time_row_beyond_data_last_when_delisted(self):
        # 같은 데이터, ms_max=None(상폐) → last (not time)
        ms, O, H, C = self._flat(10)
        C[-1] = 95.0
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=float("inf"), time_bars=26,
                      ms_max=None),
            (9, 95.0, "last"))

    def test_max_row_with_gap(self):
        # 목표 ms 20 결측 → 직전 마지막 행(idx9, ms9) 종가 청산
        ms = list(range(10)) + list(range(50, 60))
        n = len(ms)
        O = [100.0] * n
        H = [102.0] * n
        C = [100.0] * n
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=1000.0, time_bars=None,
                      max_hold=20, ms_max=1000),
            (9, 100.0, "max"))

    def test_open(self):
        ms, O, H, C = self._flat(10)
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=float("inf"), time_bars=None,
                      ms_max=50),
            (None, None, "open"))

    def test_last(self):
        ms, O, H, C = self._flat(10)
        C[-1] = 97.0
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=float("inf"), time_bars=None,
                      ms_max=200),
            (9, 97.0, "last"))
        self.assertEqual(
            self._run(ms, O, H, C, tp_px=float("inf"), time_bars=None),
            (9, 97.0, "last"))


if __name__ == "__main__":
    unittest.main()
