"""백테스트 실행기 TDD — 진입/청산/비용/지표.

실행: repo root에서
`etl\\.venv\\Scripts\\python.exe -m unittest research.cluster_closebet.tests.test_runner -v`
"""
import math
import statistics
import unittest

from research.cluster_closebet.runner import (
    PriorHighIndex,
    build_prior_high,
    build_volume_profile,
    close_position,
    entry_allowed,
    exit_allowed,
    exit_tpsl,
    net_return,
    prior_max_high,
    summarize,
    vp_overhead_ratio,
    volume_profile,
)


class TestReturn(unittest.TestCase):
    def test_deducts_roundtrip_cost(self):
        self.assertAlmostEqual(net_return(10000, 10100), 0.004)

    def test_loss(self):
        self.assertAlmostEqual(net_return(10000, 9900), -0.016)


class TestGuards(unittest.TestCase):
    def test_limit_up_entry_blocked(self):
        self.assertFalse(entry_allowed(13000, 10000))
        self.assertTrue(entry_allowed(12900, 10000))

    def test_gap_exit_blocked(self):
        self.assertFalse(exit_allowed(13500, 10000))
        self.assertTrue(exit_allowed(11000, 10000))


class TestSummarize(unittest.TestCase):
    def test_metrics(self):
        out = summarize([0.02, -0.01, 0.03])
        self.assertEqual(out["n"], 3)
        self.assertAlmostEqual(out["mean"], 0.0133333)
        self.assertAlmostEqual(out["median"], 0.02)
        self.assertAlmostEqual(out["win_rate"], 2 / 3)
        self.assertAlmostEqual(out["avg_win"], 0.025)
        self.assertAlmostEqual(out["avg_loss"], -0.01)
        self.assertAlmostEqual(out["profit_factor"], 5.0)
        self.assertAlmostEqual(out["cum"], 1.02 * 0.99 * 1.03 - 1)

    def test_mdd(self):
        out = summarize([0.02, -0.05, 0.01])
        self.assertAlmostEqual(out["mdd"], 0.05)

    def test_no_drawdown_is_zero(self):
        self.assertAlmostEqual(summarize([0.01, 0.02])["mdd"], 0.0)

    def test_buckets(self):
        out = summarize([0.035, 0.015, -0.015, -0.035])
        self.assertEqual(out["buckets"]["ge_3pct"], 1)
        self.assertEqual(out["buckets"]["ge_1pct"], 2)
        self.assertEqual(out["buckets"]["le_neg1pct"], 2)


class TestExitTpsl(unittest.TestCase):
    def _bar(self, o, h, l, c):
        return {"open_price": o, "high_price": h,
                "low_price": l, "close": c}

    def test_open_gap_above_tp(self):
        px, reason = exit_tpsl(100, [self._bar(103.5, 104, 102, 103)])
        self.assertEqual((px, reason), (103.5, "tp_open"))

    def test_open_gap_below_sl(self):
        px, reason = exit_tpsl(100, [self._bar(96.5, 97, 96, 96.5)])
        self.assertEqual((px, reason), (96.5, "sl_open"))

    def test_sl_wins_when_both_hit(self):
        px, reason = exit_tpsl(100, [self._bar(100, 104, 96, 101)])
        self.assertAlmostEqual(px, 97.0)
        self.assertEqual(reason, "sl")

    def test_tp_only(self):
        px, reason = exit_tpsl(100, [self._bar(100, 103.5, 99, 102)])
        self.assertAlmostEqual(px, 103.0)
        self.assertEqual(reason, "tp")

    def test_no_hit_h1_time_at_close(self):
        px, reason = exit_tpsl(100, [self._bar(100, 101, 99.5, 100.5)])
        self.assertEqual((px, reason), (100.5, "time"))

    def test_h2_tp_on_day2(self):
        bars = [self._bar(100, 101, 99.5, 100.5),
                self._bar(101, 103.5, 100, 103)]
        px, reason = exit_tpsl(100, bars)
        self.assertAlmostEqual(px, 103.0)
        self.assertEqual(reason, "tp")

    def test_h2_no_hit_time_at_day2_close(self):
        bars = [self._bar(100, 101, 99.5, 100.5),
                self._bar(100.5, 101.5, 99.8, 101)]
        px, reason = exit_tpsl(100, bars)
        self.assertEqual((px, reason), (101, "time"))


class TestTStat(unittest.TestCase):
    def test_value(self):
        nets = [0.02, -0.01, 0.03]
        expected = (sum(nets) / 3) / (statistics.stdev(nets) / math.sqrt(3))
        self.assertAlmostEqual(summarize(nets)["t_stat"], expected)

    def test_n_less_than_2_is_zero(self):
        self.assertEqual(summarize([])["t_stat"], 0.0)
        self.assertEqual(summarize([0.01])["t_stat"], 0.0)

    def test_zero_stdev_is_zero(self):
        self.assertEqual(summarize([0.01, 0.01])["t_stat"], 0.0)


class TestClosePosition(unittest.TestCase):
    def test_normal(self):
        row = {"close": 80, "high_price": 100, "low_price": 0}
        self.assertAlmostEqual(close_position(row), 0.8)

    def test_high_equals_low_is_none(self):
        row = {"close": 100, "high_price": 100, "low_price": 100}
        self.assertIsNone(close_position(row))


class TestPriorHigh(unittest.TestCase):
    def _rows(self):
        rows = [{"ticker": "A", "ms": ms, "high_price": 1000 + ms}
                for ms in range(1, 101)]
        rows.append({"ticker": "A", "ms": 101, "high_price": 9999})
        return rows

    def test_excludes_today(self):
        lookup = build_prior_high(self._rows())
        self.assertEqual(lookup("A", 101), 1100)

    def test_respects_ms_window(self):
        rows = self._rows()
        rows.append({"ticker": "A", "ms": 1, "high_price": 99999})
        lookup = build_prior_high(rows)
        # window=60 → ms [41, 100]; ms=1 spike is out of window
        self.assertEqual(lookup("A", 101), 1100)

    def test_none_when_fewer_than_min_obs(self):
        lookup = build_prior_high(self._rows())
        # ms [-50, 9] → only 9 rows < min_obs 40
        self.assertIsNone(lookup("A", 10))

    def test_custom_window(self):
        lookup = build_prior_high(self._rows())
        # window=10 → ms [91, 100] → 10 rows >= min_obs 10
        self.assertEqual(lookup("A", 101, window=10, min_obs=10), 1100)
        self.assertIsNone(lookup("A", 101, window=10, min_obs=11))

    def test_rows_wrapper_and_index_agree(self):
        rows = self._rows()
        self.assertEqual(prior_max_high(rows, "A", 101), 1100)
        idx = PriorHighIndex(rows)
        self.assertEqual(idx.prior_max_high("A", 101), 1100)
        self.assertEqual(idx("A", 101), 1100)
        self.assertIsNone(prior_max_high(rows, "ZZZ", 101))


class TestVolumeProfile(unittest.TestCase):
    def _window(self):
        closes = [100] * 50 + [120] * 20 + [140] * 10
        return closes, [1] * 80

    def test_bins_poc_overhead_middle(self):
        closes, volumes = self._window()
        vp = volume_profile(closes, volumes)
        self.assertEqual(vp["total"], 80)
        self.assertEqual(vp["bin_volumes"][0], 50)
        self.assertEqual(vp["bin_volumes"][20], 20)
        self.assertEqual(vp["bin_volumes"][39], 10)
        self.assertEqual(sum(vp["bin_volumes"]), 80)
        self.assertAlmostEqual(vp["poc_high"], 101)
        self.assertAlmostEqual(vp_overhead_ratio(vp, 120), 30 / 80)
        self.assertAlmostEqual(vp_overhead_ratio(vp, 100), 1.0)
        self.assertAlmostEqual(vp_overhead_ratio(vp, 141), 0.0)

    def test_fewer_than_80_none(self):
        self.assertIsNone(volume_profile([100] * 79, [1] * 79))
        lookup = build_volume_profile(
            [{"ticker": "A", "ms": i, "close": 100 + (i % 5), "volume": 1}
             for i in range(1, 80)])
        self.assertIsNone(lookup("A", 80))

    def test_hi_equals_lo_none(self):
        self.assertIsNone(volume_profile([100] * 80, [1] * 80))

    def test_zero_volume_overhead_none(self):
        vp = volume_profile([100] * 50 + [140] * 30, [0] * 80)
        self.assertIsNotNone(vp)
        self.assertIsNone(vp_overhead_ratio(vp, 120))

    def test_lookup_excludes_today_and_uses_ms_window(self):
        rows = [{"ticker": "A", "ms": ms, "close": 100 + (ms % 41),
                 "volume": 1} for ms in range(1, 122)]
        lookup = build_volume_profile(rows)
        self.assertIsNotNone(lookup("A", 121))
        self.assertIsNone(lookup("A", 80))


if __name__ == "__main__":
    unittest.main()
