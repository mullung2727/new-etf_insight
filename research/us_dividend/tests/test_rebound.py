"""rebound 합성 테스트 (DB 없음, PLAN 4절)."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.us_dividend.rebound import (
    COST,
    DIV_TAX,
    dd_bucket4,
    detect_cut,
    div_array,
    div_baseline,
    div_group,
    div_ok_mask,
    drop_entry_window,
    first_entry_per_ticker,
    fixed_target,
    market_regime,
    month_end_idx,
    payer_mask,
    retr_target,
    run_slots,
    sell_on_detect,
    simulate,
    special_suspect,
    trade_div,
    tp_exit,
    window_sum,
)

DECLARED = "CommonStockDividendsPerShareDeclared"
LB = 5
X = 0.4


def _bars(close, hold_px=None):
    """종가 → open/high/low (고가=종가×1.01, 저가=종가×0.99)."""
    c = np.array(close, dtype=float)
    return c.copy(), c * 1.01, c * 0.99, c


def _run(close, x=X, kind="fixed", val=10.0, max_hold=10, elig=None):
    o, h, lo, c = _bars(close)
    if elig is None:
        elig = np.ones(len(c), dtype=bool)
    return simulate(o, h, lo, c, np.asarray(elig), x, kind, val,
                    max_hold=max_hold, lookback=LB)


class TestFirstCrossOnly(unittest.TestCase):
    def test_1(self):
        """기준 아래 연속 3일 → 신호 1건(time 청산)."""
        close = [100] * 5 + [50, 50, 50] + [100] * 12
        ts = _run(close)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0]["sig_i"], 5)
        self.assertEqual(ts[0]["reason"], "time")


class TestReentry(unittest.TestCase):
    def test_2(self):
        """청산 후 아래 유지 → 없음, 위에서 재하락 → 신호."""
        close = ([100] * 5 + [50] * 6 + [25] * 3 + [100] * 3
                 + [50] + [50] * 7)
        ts = _run(close, max_hold=5)
        self.assertEqual([t["sig_i"] for t in ts], [5, 17])


class TestHoldingIgnores(unittest.TestCase):
    def test_3(self):
        """보유 중 회복·재하락 → 추가 진입 없음."""
        close = [100] * 5 + [50, 50, 100, 50] + [50] * 5 + [100] * 6
        ts = _run(close, max_hold=8)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0]["entry_i"], 6)


class TestEligible(unittest.TestCase):
    def test_4a(self):
        """eligible False 날 하락 → 신호 아님."""
        close = [100] * 5 + [50, 50, 50] + [100] * 12
        elig = np.ones(len(close), dtype=bool)
        elig[5] = False
        self.assertEqual(_run(close, elig=elig), [])

    def test_4b(self):
        """filed 당일 False, 다음 날부터 True."""
        rows = pd.DataFrame([
            {"cik": "1", "tag": DECLARED, "end": "2021-12-31",
             "filed": "2022-02-15", "val": 1.0},
            {"cik": "1", "tag": DECLARED, "end": "2022-12-31",
             "filed": "2023-02-15", "val": 1.0},
            {"cik": "1", "tag": DECLARED, "end": "2023-12-31",
             "filed": "2024-02-15", "val": 1.0},
        ])
        m = div_ok_mask(["20240214", "20240215", "20240216", "20240217"],
                        rows, None)
        self.assertEqual(m.tolist(), [False, False, True, True])


class TestTpExit(unittest.TestCase):
    def test_5a_gap(self):
        """진입일 고가 무시, 다음 날 시가 ≥ 목표 → 시가·tp_gap."""
        o = [0.0, 0.0, 100.0, 112.0, 100.0, 100.0, 100.0, 100.0]
        h = [0.0, 0.0, 150.0, 115.0, 105.0, 105.0, 105.0, 105.0]
        c = [0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        self.assertEqual(tp_exit(o, h, c, 2, 110.0, 5), (3, 112.0, "tp_gap"))

    def test_5b_touch(self):
        """고가만 닿음 → 목표가·tp."""
        o = [0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        h = [0.0, 0.0, 150.0, 112.0, 105.0, 105.0, 105.0, 105.0]
        c = [0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        self.assertEqual(tp_exit(o, h, c, 2, 110.0, 5), (3, 110.0, "tp"))


class TestMaxHold(unittest.TestCase):
    def test_6_time(self):
        """60번째 거래일 종가 time 청산."""
        n = 60
        o = np.full(n, 100.0)
        h = np.full(n, 105.0)
        c = np.full(n, 100.0)
        c[59] = 90.0
        self.assertEqual(tp_exit(o, h, c, 0, 110.0, 60), (59, 90.0, "time"))

    def test_6_none(self):
        """데이터 부족 → None."""
        n = 59
        o = np.full(n, 100.0)
        h = np.full(n, 105.0)
        c = np.full(n, 100.0)
        self.assertIsNone(tp_exit(o, h, c, 0, 110.0, 60))

    def test_6c_expiry_nan(self):
        """만료일 NaN → 마지막 유한 종가로 time 청산."""
        n = 60
        o = np.full(n, 100.0)
        h = np.full(n, 105.0)
        c = np.full(n, 100.0)
        c[58] = 95.0
        c[59] = np.nan
        self.assertEqual(tp_exit(o, h, c, 0, 110.0, 60), (58, 95.0, "time"))

    def test_6d_expiry_all_nan(self):
        """e..k_end 전부 NaN → None."""
        n = 60
        o = np.full(n, 100.0)
        h = np.full(n, 105.0)
        c = np.full(n, np.nan)
        self.assertIsNone(tp_exit(o, h, c, 0, 110.0, 60))


class TestTargets(unittest.TestCase):
    def test_7(self):
        """되돌림 1/3=73.33, 1/2=80."""
        self.assertAlmostEqual(retr_target(60, 100, 1 / 3), 73.3333333333)
        self.assertAlmostEqual(retr_target(60, 100, 1 / 2), 80.0)
        self.assertAlmostEqual(fixed_target(100, 0.15), 115.0)


class TestCost(unittest.TestCase):
    def test_8(self):
        """ret = exit/entry − 1 − 0.0035."""
        self.assertAlmostEqual(COST, 0.0035)
        o = [0.0, 0.0, 50.0, 60.0, 60.0, 60.0, 60.0, 60.0]
        h = [0.0, 0.0, 50.0, 80.0, 60.0, 60.0, 60.0, 60.0]
        c = [0.0, 0.0, 50.0, 60.0, 60.0, 60.0, 60.0, 60.0]
        k, px, reason = tp_exit(o, h, c, 2, fixed_target(50.0, 0.5), 5)
        self.assertEqual((k, reason), (3, "tp"))
        self.assertAlmostEqual(px / 50.0 - 1 - COST, 0.4965)


class TestMaxHold20(unittest.TestCase):
    def test_9a_time_20(self):
        """20번째 거래일(진입 포함) 종가 time 청산."""
        n = 20
        o = np.full(n, 100.0)
        h = np.full(n, 105.0)
        c = np.full(n, 100.0)
        c[19] = 90.0
        self.assertEqual(tp_exit(o, h, c, 0, 110.0, 20), (19, 90.0, "time"))

    def test_9b_none_20(self):
        """데이터 부족 → None."""
        n = 19
        o = np.full(n, 100.0)
        h = np.full(n, 105.0)
        c = np.full(n, 100.0)
        self.assertIsNone(tp_exit(o, h, c, 0, 110.0, 20))


class TestX080(unittest.TestCase):
    def test_10(self):
        """고점 100 → 19 하락 시 X=0.80 신호 1건."""
        close = [100] * 5 + [19, 19, 19] + [100] * 12
        ts = _run(close, x=0.80)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0]["sig_i"], 5)


class TestTpInf(unittest.TestCase):
    def test_11(self):
        """tp=inf → 갭·고가 무시, 항상 time 청산."""
        o = [0.0, 0.0, 100.0, 500.0, 100.0, 100.0, 100.0, 100.0]
        h = [0.0, 0.0, 150.0, 600.0, 105.0, 105.0, 105.0, 105.0]
        c = [0.0, 0.0, 100.0, 100.0, 100.0, 100.0, 100.0, 95.0]
        self.assertEqual(tp_exit(o, h, c, 2, float("inf"), 5),
                         (6, 100.0, "time"))


class TestDropEntryWindow(unittest.TestCase):
    def test_12(self):
        """경계 포함 제외, 바깥 유지."""
        axis = ["20200131", "20200201", "20200315", "20200630", "20200701"]
        ts = [{"entry_i": i} for i in range(5)]
        kept = drop_entry_window(ts, axis, "20200201", "20200630")
        self.assertEqual([t["entry_i"] for t in kept], [0, 4])


class TestFirstEntryPerTicker(unittest.TestCase):
    def test_13(self):
        """같은 ticker 3건 → 가장 이른 1건, 다른 ticker 유지."""
        ts = [{"ticker": "A", "entry_i": 5}, {"ticker": "A", "entry_i": 2},
              {"ticker": "A", "entry_i": 9}, {"ticker": "B", "entry_i": 7}]
        got = sorted(first_entry_per_ticker(ts),
                     key=lambda t: t["ticker"])
        self.assertEqual([(t["ticker"], t["entry_i"]) for t in got],
                         [("A", 2), ("B", 7)])


class TestArrayX(unittest.TestCase):
    def test_14_scalar_vs_array(self):
        """스칼라 x와 같은 값 배열 x → 동일 결과."""
        close = [100] * 5 + [50, 50, 50] + [100] * 12
        self.assertEqual(_run(close, x=X),
                         _run(close, x=np.full(len(close), X)))

    def test_15_jump_no_signal(self):
        """X 전환일 기준선 점프만으로 신호 없음."""
        close = [100] * 5 + [40] * 10
        xx = np.array([0.7] * 8 + [0.5] * 7)
        self.assertEqual(_run(close, x=xx), [])
        self.assertEqual([t["sig_i"] for t in _run(close, x=0.5)], [5])


class TestMarketRegime(unittest.TestCase):
    def test_16_hi252(self):
        """100 → 89 True, 91 False."""
        self.assertTrue(market_regime(
            np.array([100.0] * 252 + [89.0]), "hi252")[252])
        self.assertFalse(market_regime(
            np.array([100.0] * 252 + [91.0]), "hi252")[252])

    def test_17_fast20(self):
        """판정 1일 → 정확히 60일 True, 61번째 False."""
        m = market_regime(
            np.array([100.0] * 20 + [89.0] + [100.0] * 100), "fast20")
        self.assertEqual(int(m.sum()), 60)
        self.assertTrue(m[20])
        self.assertFalse(m[19])
        self.assertFalse(m[80])

    def test_18_bad_kind(self):
        """잘못된 kind → ValueError."""
        with self.assertRaises(ValueError):
            market_regime(np.array([1.0, 2.0]), "nope")


class TestRunSlots(unittest.TestCase):
    def test_19_depth_priority(self):
        """slots=2·같은 진입일 3건 → 깊은 2건 체결·1건 건너뜀, 청산 후 equity=cash."""
        n = 10
        cb = {t: np.full(n, 100.0) for t in "ABC"}
        fee = 0.00175

        def tr(tk, depth, xe):
            return {"ticker": tk, "entry_i": 1, "exit_i": xe, "entry": 100.0,
                    "exit_px": 110.0, "depth": depth}

        r = run_slots([tr("A", -0.5, 3), tr("B", -0.3, 3), tr("C", -0.1, 4)],
                      cb, n, slots=2, fee=fee)
        self.assertEqual(r["taken"], 2)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual([t["ticker"] for t in r["taken_trades"]], ["A", "B"])
        self.assertAlmostEqual(r["equity"][3], 1.1 * (1 - fee) ** 2)
        self.assertAlmostEqual(r["equity"][9], r["equity"][3])
        self.assertAlmostEqual(r["invested"][1], 1.0)
        self.assertAlmostEqual(r["invested"][3], 0.0)

    def test_20_same_ticker_skip(self):
        """보유 중 같은 ticker 다른 건 → 건너뜀."""
        n = 8
        cb = {"A": np.full(n, 100.0)}
        r = run_slots(
            [{"ticker": "A", "entry_i": 1, "exit_i": 5, "entry": 100.0,
              "exit_px": 100.0, "depth": -0.5},
             {"ticker": "A", "entry_i": 2, "exit_i": 6, "entry": 100.0,
              "exit_px": 100.0, "depth": -0.6}],
            cb, n, slots=10)
        self.assertEqual(r["taken"], 1)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(r["taken_trades"][0]["entry_i"], 1)


class TestDivArray(unittest.TestCase):
    def test_21_next_trading_day(self):
        """휴장일 배당락 → 다음 거래일 칸."""
        axis = ["20200102", "20200103", "20200106"]
        got = div_array(axis, ["20200104"], [2.0])
        self.assertEqual(got.tolist(), [0.0, 0.0, 2.0])

    def test_22_same_cell_sum(self):
        """같은 칸이면 합산."""
        axis = ["20200102", "20200103", "20200106"]
        got = div_array(axis, ["20200103", "20200103"], [1.0, 2.0])
        self.assertEqual(got.tolist(), [0.0, 3.0, 0.0])

    def test_23_out_of_range_drop(self):
        """축 범위 밖은 버림."""
        axis = ["20200102", "20200103", "20200106"]
        got = div_array(axis, ["20191231", "20200107"], [5.0, 9.0])
        self.assertEqual(got.tolist(), [0.0, 0.0, 0.0])


class TestTradeDiv(unittest.TestCase):
    def test_24_bounds(self):
        """d==e 제외, d==k 포함, e<d<k 포함, k 이후 제외."""
        div = np.array([7.0, 99.0, 1.0, 2.0, 8.0, 4.0])
        self.assertAlmostEqual(trade_div(div, 1, 3), 3.0)
        self.assertAlmostEqual(trade_div(div, 1, 1), 0.0)
        self.assertAlmostEqual(trade_div(div, 0, 1), 99.0)
        self.assertAlmostEqual(trade_div(div, 3, 5), 12.0)


class TestRunSlotsDividend(unittest.TestCase):
    def test_25_cash(self):
        """보유 중 배당 → cash에 shares×배당×0.85, 진입일분 제외·청산일분 포함."""
        self.assertAlmostEqual(DIV_TAX, 0.15)
        n = 6
        fee = 0.00175
        cb = {"A": np.full(n, 100.0)}
        tr = [{"ticker": "A", "entry_i": 1, "exit_i": 4, "entry": 100.0,
               "exit_px": 110.0, "depth": 0.0}]
        div = np.zeros(n)
        div[1] = 5.0
        div[3] = 1.0
        div[4] = 2.0
        r = run_slots(tr, cb, n, div_by_ticker={"A": div})
        shares = 0.1 * (1 - fee) / 100.0
        want = 0.9 + shares * (1.0 + 2.0) * 0.85 + shares * 110.0 * (1 - fee)
        self.assertAlmostEqual(r["equity"][4], want)
        self.assertAlmostEqual(r["equity"][5], want)

    def test_26_none_same(self):
        """div None이면 기존 결과와 동일."""
        n = 6
        fee = 0.00175
        cb = {"A": np.full(n, 100.0)}
        tr = [{"ticker": "A", "entry_i": 1, "exit_i": 4, "entry": 100.0,
               "exit_px": 110.0, "depth": 0.0}]
        r = run_slots(tr, cb, n, div_by_ticker=None)
        shares = 0.1 * (1 - fee) / 100.0
        want = 0.9 + shares * 110.0 * (1 - fee)
        self.assertAlmostEqual(r["equity"][4], want)
        self.assertEqual(r["taken"], 1)

    def test_27_exit_day_div(self):
        """청산일 배당락만 → 세후 수령 후 청산, div_tax=0이면 전액."""
        n = 6
        fee = 0.00175
        cb = {"A": np.full(n, 100.0)}
        tr = [{"ticker": "A", "entry_i": 1, "exit_i": 4, "entry": 100.0,
               "exit_px": 110.0, "depth": 0.0}]
        div = np.zeros(n)
        div[4] = 2.0
        shares = 0.1 * (1 - fee) / 100.0
        r = run_slots(tr, cb, n, div_by_ticker={"A": div})
        self.assertAlmostEqual(
            r["equity"][4],
            0.9 + shares * 2.0 * 0.85 + shares * 110.0 * (1 - fee))
        r0 = run_slots(tr, cb, n, div_by_ticker={"A": div}, div_tax=0.0)
        self.assertAlmostEqual(
            r0["equity"][4],
            0.9 + shares * 2.0 + shares * 110.0 * (1 - fee))


class TestMonthEndIdx(unittest.TestCase):
    def test_28_two_months(self):
        """두 달치 축 → 각 달 마지막 날 인덱스."""
        axis = ["20200130", "20200131", "20200227", "20200228", "20200302"]
        self.assertEqual(month_end_idx(axis, "202001", "202002"), [1, 3])

    def test_29_out_of_range(self):
        """범위 밖 달 제외."""
        axis = ["20200130", "20200131", "20200227", "20200228", "20200302"]
        self.assertEqual(month_end_idx(axis, "202002", "202002"), [3])
        self.assertEqual(month_end_idx(axis, "202004", "202005"), [])


class TestWindowSum(unittest.TestCase):
    def test_30_hand(self):
        """손계산: 직전 div[0..2]=6, 다음 div[3..5]=15."""
        div = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        prev, nxt = window_sum(div, 2, w=3)
        self.assertAlmostEqual(prev, 6.0)
        self.assertAlmostEqual(nxt, 15.0)

    def test_31_next_nan(self):
        """i+w 범위 밖 → next NaN, prev 정상."""
        div = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        prev, nxt = window_sum(div, 3, w=3)
        self.assertAlmostEqual(prev, 9.0)
        self.assertTrue(np.isnan(nxt))


class TestSpecialSuspect(unittest.TestCase):
    def test_32_true(self):
        """[1,1,1,5] → True."""
        self.assertTrue(
            special_suspect(np.array([1.0, 1.0, 1.0, 5.0]), 3))

    def test_33_flat_false(self):
        """[1,1,1,1] → False."""
        self.assertFalse(
            special_suspect(np.array([1.0, 1.0, 1.0, 1.0]), 3))

    def test_34_two_false(self):
        """0 아닌 배당 2회 → False."""
        self.assertFalse(
            special_suspect(np.array([0.0, 0.0, 1.0, 5.0]), 3))


class TestDdBucket4(unittest.TestCase):
    def test_35_bounds(self):
        """−0.2 → "-20~-40", −0.1999 → "0~-20", −0.6 → "-60+"."""
        self.assertEqual(dd_bucket4(-0.2), "-20~-40")
        self.assertEqual(dd_bucket4(-0.1999), "0~-20")
        self.assertEqual(dd_bucket4(-0.6), "-60+")


class TestDivBaseline(unittest.TestCase):
    def test_36_hand(self):
        """위치 [10,73,136] → (마지막 금액, 63, 136)."""
        div = np.zeros(200)
        div[10] = 1.0
        div[73] = 1.0
        div[136] = 2.5
        got = div_baseline(div, 136)
        self.assertAlmostEqual(got[0], 2.5)
        self.assertAlmostEqual(got[1], 63.0)
        self.assertEqual(got[2], 136)

    def test_37_single_none(self):
        """1회 → None."""
        div = np.zeros(200)
        div[50] = 1.0
        self.assertIsNone(div_baseline(div, 100))
        self.assertIsNone(div_baseline(np.zeros(200), 100))


def _cut_div():
    """기준 1.0·간격 63 (위치 10·73)."""
    div = np.zeros(300)
    div[10] = 1.0
    div[73] = 1.0
    return div


class TestDetectCut(unittest.TestCase):
    def test_38_cut(self):
        """보유 중 0.5 배당 → (그 위치, "cut")."""
        div = _cut_div()
        div[100] = 0.5
        self.assertEqual(detect_cut(div, 73, 74, 150), (100, "cut"))

    def test_39_keep_none(self):
        """0.95 배당 → 감지 없음."""
        div = _cut_div()
        div[100] = 0.95
        self.assertIsNone(detect_cut(div, 73, 74, 120))

    def test_40_omit(self):
        """마지막 배당 뒤 95일 넘게 없음 → (그 위치, "omit")."""
        div = _cut_div()
        self.assertEqual(detect_cut(div, 73, 74, 200), (168, "omit"))

    def test_41_single_history_none(self):
        """이력 1회 → None."""
        div = np.zeros(200)
        div[50] = 1.0
        div[80] = 0.1
        self.assertIsNone(detect_cut(div, 50, 51, 100))


class TestSellOnDetect(unittest.TestCase):
    def test_42_beyond(self):
        """det_i+1이 원래 청산일 넘으면 원본 그대로."""
        tr = {"entry_i": 10, "exit_i": 20, "entry": 100.0,
              "exit_px": 110.0, "ret": 110.0 / 100.0 - 1 - COST,
              "hold": 11, "reason": "time"}
        o = np.full(30, 105.0)
        got = sell_on_detect(tr, o, 20)
        self.assertEqual(got, tr)
        self.assertIsNot(got, tr)

    def test_43_sell(self):
        """아니면 exit_i·ret·reason 갱신."""
        tr = {"entry_i": 10, "exit_i": 20, "entry": 100.0,
              "exit_px": 110.0, "ret": 110.0 / 100.0 - 1 - COST,
              "hold": 11, "reason": "time"}
        o = np.full(30, 100.0)
        o[16] = 105.0
        got = sell_on_detect(tr, o, 15)
        self.assertEqual(got["exit_i"], 16)
        self.assertAlmostEqual(got["exit_px"], 105.0)
        self.assertEqual(got["reason"], "cut_sell")
        self.assertAlmostEqual(got["ret"], 105.0 / 100.0 - 1 - COST)
        self.assertEqual(got["hold"], 7)


class TestDivGroup(unittest.TestCase):
    def test_44_stable(self):
        """stable True → "안정배당" (prev 무관)."""
        self.assertEqual(div_group(True, 0.0), "안정배당")
        self.assertEqual(div_group(True, 5.0), "안정배당")

    def test_45_unstable(self):
        """stable False + prev > 0 → "불안정배당"."""
        self.assertEqual(div_group(False, 1.5), "불안정배당")

    def test_46_none(self):
        """prev 0·NaN → "무배당"."""
        self.assertEqual(div_group(False, 0.0), "무배당")
        self.assertEqual(div_group(False, float("nan")), "무배당")


class TestPayerMask(unittest.TestCase):
    def test_47_no_div_false(self):
        """stable False·배당 없음 → 전부 False."""
        n = 300
        m = payer_mask(np.zeros(n, dtype=bool), np.zeros(n))
        self.assertEqual(m.dtype, bool)
        self.assertFalse(m.any())

    def test_48_window_edge(self):
        """배당 1회 후 251일째 True, 252일째(창 밖) False."""
        n = 300
        div = np.zeros(n)
        div[0] = 1.0
        m = payer_mask(np.zeros(n, dtype=bool), div)
        self.assertTrue(m[0])
        self.assertTrue(m[251])
        self.assertFalse(m[252])

    def test_49_stable_true(self):
        """stable True면 배당 없어도 True."""
        n = 10
        m = payer_mask(np.ones(n, dtype=bool), np.zeros(n))
        self.assertTrue(m.all())


if __name__ == "__main__":
    unittest.main()
