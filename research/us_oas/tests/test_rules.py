"""rules 단위 테스트 — 합성 OAS만, DB·네트워크 없음 (unittest)."""
import unittest

import numpy.testing
import pandas as pd

from research.us_oas import rules


def _oas(vals, start="2024-01-01"):
    base = pd.Timestamp(start)
    idx = [(base + pd.Timedelta(days=k)).strftime("%Y%m%d") for k in range(len(vals))]
    return pd.Series(list(vals), index=idx)


def _next_day(day):
    return (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y%m%d")


class TestRules(unittest.TestCase):
    def test_t8_no_lookahead(self):
        oas = _oas([3.0] * 20)
        dates = oas.index[5:10].tolist()
        base = rules.align_oas(oas, dates)
        mod = oas.copy()
        t = dates[2]
        mod.loc[t] = 99.0
        changed = rules.align_oas(mod, dates)
        pd.testing.assert_frame_equal(changed.loc[[t]], base.loc[[t]])
        self.assertNotEqual(changed.loc[dates[3], "oas"], base.loc[dates[3], "oas"])

    def test_t11_no_revert_without_e(self):
        self.assertEqual(rules.spec_state(3.8, 80.0, "A"), "B")

    def test_t12_enter_and_hold_e(self):
        self.assertEqual(rules.spec_state(8.5, 200.0, "C"), "E")
        self.assertEqual(rules.spec_state(7.0, 10.0, "E"), "E")

    def test_t13_revert_after_e(self):
        self.assertEqual(rules.spec_state(4.4, 0.0, "E"), "A")
        self.assertEqual(rules.spec_state(6.5, -60.0, "E"), "A")

    def test_t14_gap_holds_prev(self):
        self.assertEqual(rules.spec_state(5.5, 80.0, "C"), "C")

    def test_b1_bollinger_regime(self):
        vals = [2.99, 3.01] * 62 + [3.0, 3.0] + [4.0]
        oas = _oas(vals)
        d_normal = _next_day(oas.index[125])
        d_off = _next_day(oas.index[126])
        reg = rules.boll_regime(rules.align_oas(oas, [d_normal, d_off]))
        self.assertEqual(reg.loc[d_normal], "normal")
        self.assertEqual(reg.loc[d_off], "risk_off")
        short = rules.boll_regime(rules.align_oas(_oas([3.0] * 100), [_next_day(_oas([0] * 100).index[-1])]))
        self.assertEqual(short.iloc[0], "normal")

    def test_b2_regime_no_lookahead(self):
        oas = _oas([3.0] * 130)
        dates = oas.index[126:129].tolist()
        base = rules.boll_regime(rules.align_oas(oas, dates))
        mod = oas.copy()
        mod.loc[dates[1]] = 99.0
        changed = rules.boll_regime(rules.align_oas(mod, dates))
        self.assertEqual(changed.loc[dates[1]], base.loc[dates[1]])
        self.assertEqual(changed.loc[dates[2]], "risk_off")

    def test_b3_bollinger_weights(self):
        aligned = pd.DataFrame(
            {"oas": [4.0, 3.0], "upper": [3.5, 3.5], "lower": [2.5, 2.5]},
            index=["20240102", "20240103"],
        )
        a = rules.boll_weights(aligned, "A")
        b = rules.boll_weights(aligned, "B")
        self.assertAlmostEqual(a.loc["20240102", "TQQQ"], 0.4)
        self.assertAlmostEqual(a.loc["20240102", "QQQ"], 0.6)
        self.assertAlmostEqual(b.loc["20240102", "TQQQ"], 0.7)
        self.assertAlmostEqual(b.loc["20240102", "QQQ"], 0.3)
        self.assertAlmostEqual(a.loc["20240103", "TQQQ"], 1.0)
        self.assertAlmostEqual(a.loc["20240103", "QQQ"], 0.0)

    def test_spec_weights_mapping(self):
        aligned = pd.DataFrame(
            {"oas": [3.0, 3.8, 3.9, 3.9, 8.5],
             "d10_bp": [0.0, 80.0, 120.0, 160.0, 200.0]},
            index=["20240102", "20240103", "20240104", "20240105", "20240108"],
        )
        w = rules.spec_weights(aligned)
        numpy.testing.assert_allclose(w["TQQQ"].to_numpy(), [1.0, 0.7, 0.4, 0.2, 0.7])
        numpy.testing.assert_allclose(w["QQQ"].to_numpy(), [0.0, 0.3, 0.6, 0.8, 0.3])


class TestHold(unittest.TestCase):
    def test_h1_below_ma(self):
        level = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 4.0, 3.0, 2.0, 1.0])
        b = rules.below_ma(level, window=3)
        # idx5: 4 < mean(4,5,4)=4.33
        self.assertEqual(b.tolist(), [False] * 5 + [True] * 4)

    def test_h2_hold_while_below(self):
        w = pd.Series([1.0, 0.7, 1.0, 1.0, 0.4, 1.0])
        below = pd.Series([False, True, True, False, True, True])
        numpy.testing.assert_allclose(
            rules.hold_while_below(w, below).to_numpy(),
            [1.0, 0.7, 0.7, 1.0, 0.4, 0.4])

    def test_h3_hold_index_mismatch(self):
        w = pd.Series([1.0, 0.7], index=["20240102", "20240103"])
        below = pd.Series([False, True], index=["20240102", "20240104"])
        with self.assertRaises(ValueError):
            rules.hold_while_below(w, below)


class TestCombine(unittest.TestCase):
    def test_g1_above_uses_oas_only(self):
        w_oas = pd.Series([1.0, 0.7, 0.2], index=["20240102", "20240103", "20240104"])
        below = pd.Series([False, False, False], index=w_oas.index)
        w = rules.combine_weights(w_oas, below, 0.4, "BIL")
        self.assertEqual(list(w.columns), ["TQQQ", "QQQ", "BIL"])
        numpy.testing.assert_allclose(w["TQQQ"].to_numpy(), [1.0, 0.7, 0.2])
        numpy.testing.assert_allclose(w["QQQ"].to_numpy(), [0.0, 0.3, 0.8])
        numpy.testing.assert_allclose(w["BIL"].to_numpy(), [0.0, 0.0, 0.0])

    def test_g2_below_bil_no_oas_cut(self):
        w_oas = pd.Series([1.0], index=["20240102"])
        below = pd.Series([True], index=w_oas.index)
        w = rules.combine_weights(w_oas, below, 0.4, "BIL")
        numpy.testing.assert_allclose(w.loc["20240102"].to_numpy(), [0.4, 0.0, 0.6])

    def test_g3_below_bil_with_oas_cut(self):
        w_oas = pd.Series([0.2], index=["20240102"])
        below = pd.Series([True], index=w_oas.index)
        w = rules.combine_weights(w_oas, below, 0.4, "BIL")
        numpy.testing.assert_allclose(w.loc["20240102"].to_numpy(), [0.2, 0.2, 0.6])

    def test_g4_below_qqq_defense(self):
        w_oas = pd.Series([0.7], index=["20240102"])
        below = pd.Series([True], index=w_oas.index)
        w = rules.combine_weights(w_oas, below, 0.4, "QQQ")
        numpy.testing.assert_allclose(w.loc["20240102"].to_numpy(), [0.4, 0.6, 0.0])

    def test_g5_bad_defense(self):
        w_oas = pd.Series([1.0], index=["20240102"])
        below = pd.Series([True], index=w_oas.index)
        with self.assertRaises(ValueError):
            rules.combine_weights(w_oas, below, 0.4, "CASH")


if __name__ == "__main__":
    unittest.main()
