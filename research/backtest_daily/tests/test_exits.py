"""exits 단위 테스트 — 합성 데이터만, 실 DB 없음 (unittest)."""
import unittest

import pandas as pd

from research.backtest_daily.exits import hold_exit, trailing_exit


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


if __name__ == "__main__":
    unittest.main()
