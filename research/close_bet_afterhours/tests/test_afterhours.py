"""시간외 종가배팅 TDD — 순수 함수만, 네트워크·DB 없음.

실행: repo root에서
`etl\\.venv\\Scripts\\python.exe -m unittest research.close_bet_afterhours.tests.test_afterhours -v`
"""
import math
import statistics
import unittest

from research.close_bet_afterhours.afterhours import (
    ROUNDTRIP_COST,
    filter_afterhours_bars,
    net_return,
    select_candidates,
    simulate_exit,
    summarize,
    t_stat,
)


def _bar(time, o, h, low, c, vol=10, date="20260601"):
    return {"timestamp": date + time, "date": date, "time": time,
            "open": o, "high": h, "low": low, "close": c, "volume": vol}


class TestSimulateExit(unittest.TestCase):
    def test_gap_above_tp_at_open(self):
        bars = [_bar("154000", 10200, 10250, 10150, 10200)]
        self.assertEqual(simulate_exit(10000, bars, 0.01, 0.01), (10200, "tp_open"))

    def test_gap_below_sl_at_open(self):
        bars = [_bar("154000", 9800, 9850, 9750, 9800)]
        self.assertEqual(simulate_exit(10000, bars, 0.01, 0.01), (9800, "sl_open"))

    def test_same_bar_both_hit_is_sl(self):
        bars = [_bar("154000", 10000, 10200, 9800, 10050)]
        self.assertEqual(simulate_exit(10000, bars, 0.01, 0.01), (9900, "sl"))

    def test_tp_inside_bar(self):
        bars = [_bar("154000", 10000, 10150, 9950, 10050)]
        self.assertEqual(simulate_exit(10000, bars, 0.01, 0.01), (10100, "tp"))

    def test_sl_inside_bar(self):
        bars = [_bar("154000", 10000, 10050, 9850, 9990)]
        self.assertEqual(simulate_exit(10000, bars, 0.01, 0.01), (9900, "sl"))

    def test_no_hit_exits_last_close(self):
        bars = [_bar("154000", 10000, 10050, 9950, 10020),
                _bar("154100", 10020, 10060, 9990, 10040)]
        self.assertEqual(simulate_exit(10000, bars, 0.01, 0.01), (10040, "time"))

    def test_empty_path_is_none(self):
        self.assertIsNone(simulate_exit(10000, [], 0.01, 0.01))

    def test_first_hit_bar_wins(self):
        bars = [_bar("154000", 10000, 10050, 9950, 10020),
                _bar("154100", 10020, 10300, 10000, 10250)]
        self.assertEqual(simulate_exit(10000, bars, 0.01, 0.01), (10100, "tp"))


class TestNetReturn(unittest.TestCase):
    def test_applies_roundtrip_cost(self):
        self.assertAlmostEqual(net_return(10000, 10100), 0.01 - 0.006)
        self.assertAlmostEqual(net_return(10000, 10000), -0.006)

    def test_cost_constant(self):
        self.assertEqual(ROUNDTRIP_COST, 0.006)


class TestSummarize(unittest.TestCase):
    NETS = [0.02, -0.01, 0.03, -0.02, 0.01]

    def test_metrics(self):
        s = summarize(self.NETS)
        self.assertEqual(s["n"], 5)
        self.assertAlmostEqual(s["mean"], 0.006)
        self.assertAlmostEqual(s["median"], 0.01)
        self.assertAlmostEqual(s["win_rate"], 0.6)
        self.assertAlmostEqual(s["avg_win"], 0.02)
        self.assertAlmostEqual(s["avg_loss"], -0.015)
        self.assertAlmostEqual(s["profit_factor"], 2.0)
        expected_t = (0.006
                      / (statistics.stdev(self.NETS) / math.sqrt(5)))
        self.assertAlmostEqual(s["t_stat"], expected_t)

    def test_t_stat_needs_two_points(self):
        self.assertEqual(summarize([0.01])["t_stat"], 0.0)
        self.assertEqual(summarize([])["t_stat"], 0.0)
        self.assertEqual(t_stat([0.01]), 0.0)

    def test_zero_variance_t_is_zero(self):
        self.assertEqual(summarize([0.01, 0.01])["t_stat"], 0.0)

    def test_empty(self):
        s = summarize([])
        self.assertEqual(s["n"], 0)
        self.assertEqual(s["win_rate"], 0.0)
        self.assertEqual(s["profit_factor"], 0.0)


class TestSelectCandidates(unittest.TestCase):
    def test_top3_per_date_score_desc_ticker_asc(self):
        rows = [
            {"date": "20260601", "ticker": "B", "score": 5},
            {"date": "20260601", "ticker": "A", "score": 5},
            {"date": "20260601", "ticker": "C", "score": 3},
            {"date": "20260601", "ticker": "D", "score": 1},
            {"date": "20260601", "ticker": "E", "score": -1},
            {"date": "20260602", "ticker": "Z", "score": 0},
            {"date": "20260602", "ticker": "Y", "score": 2},
        ]
        got = [(c["date"], c["ticker"]) for c in select_candidates(rows)]
        self.assertEqual(got, [("20260601", "A"), ("20260601", "B"),
                               ("20260601", "C"), ("20260602", "Y"),
                               ("20260602", "Z")])

    def test_negative_and_none_excluded(self):
        rows = [{"date": "20260601", "ticker": "A", "score": None},
                {"date": "20260601", "ticker": "B", "score": -1}]
        self.assertEqual(select_candidates(rows), [])


class TestFilterBars(unittest.TestCase):
    def test_only_that_date_within_window(self):
        bars = [
            _bar("153900", 100, 100, 100, 100),
            _bar("154000", 100, 100, 100, 100),
            _bar("180000", 101, 101, 101, 101),
            _bar("195500", 102, 102, 102, 102),
            _bar("195600", 103, 103, 103, 103),
            _bar("180000", 200, 200, 200, 200, date="20260602"),
        ]
        got = filter_afterhours_bars(bars, "20260601", "195500")
        self.assertEqual([b["time"] for b in got],
                         ["154000", "180000", "195500"])

    def test_sorted_ascending(self):
        bars = [_bar("180000", 101, 101, 101, 101),
                _bar("154000", 100, 100, 100, 100)]
        got = filter_afterhours_bars(bars, "20260601", "195500")
        self.assertEqual([b["time"] for b in got], ["154000", "180000"])

    def test_custom_cutoff(self):
        bars = [_bar("180000", 101, 101, 101, 101),
                _bar("190000", 102, 102, 102, 102)]
        got = filter_afterhours_bars(bars, "20260601", "183000")
        self.assertEqual([b["time"] for b in got], ["180000"])

    def test_empty(self):
        self.assertEqual(filter_afterhours_bars([], "20260601", "195500"), [])


if __name__ == "__main__":
    unittest.main()
