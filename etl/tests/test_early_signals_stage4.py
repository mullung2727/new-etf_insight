"""4단계 수용 테스트 — 선행 발견·추천 이후 결과 (PLAN §20-4).

T02 원문 전 이미 급등 → after_move · T25 장 마감 뒤 보고서는 다음 거래일 시가
T26 같은 가설 매주 선정은 episode 중복 아님 · T42 권리변동 보류 · T43 시총 구간
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402

from early_signals import evaluation  # noqa: E402

SESSIONS = [f"202607{d:02d}" for d in range(1, 29)] + [f"202608{d:02d}" for d in range(1, 29)]


def bars(sessions, price=1000, shares=1_000_000):
    return {d: {"date": d, "open": price, "high": price, "low": price, "close": price,
                "volume": 10, "trading_value": 2_000_000_000,
                "market_cap": 100_000_000_000, "list_shrs": shares}
            for d in sessions}


class SurgeTest(unittest.TestCase):
    def test_surge_needs_25pct_over_20_sessions(self):
        daily = bars(SESSIONS)
        for date in SESSIONS[21:]:
            daily[date]["close"] = 1300      # +30%
            daily[date]["high"] = daily[date]["low"] = daily[date]["open"] = 1300
        episodes = evaluation.detect_surge_episodes(SESSIONS, daily)
        self.assertTrue(episodes)
        self.assertEqual(episodes[0]["confirm_date"], SESSIONS[21])

    def test_below_threshold_is_not_an_episode(self):
        daily = bars(SESSIONS)
        for date in SESSIONS[21:]:
            for key in ("open", "high", "low", "close"):
                daily[date][key] = 1200      # +20%
        self.assertEqual(evaluation.detect_surge_episodes(SESSIONS, daily), [])

    def test_t42_share_jump_suppresses_fake_surge(self):
        """주식수만 바뀐 가격 점프는 급등으로 세지 않는다."""
        daily = bars(SESSIONS)
        for date in SESSIONS[21:]:
            for key in ("open", "high", "low", "close"):
                daily[date][key] = 1300
            daily[date]["list_shrs"] = 5_000_000      # 밴드 밖
        self.assertEqual(evaluation.detect_surge_episodes(SESSIONS, daily), [])

    def test_consecutive_days_are_one_episode(self):
        daily = bars(SESSIONS)
        for date in SESSIONS[21:]:
            for key in ("open", "high", "low", "close"):
                daily[date][key] = 1300
        self.assertEqual(len(evaluation.detect_surge_episodes(SESSIONS, daily)), 1)


class DetectionTest(unittest.TestCase):
    def test_t02_observed_after_surge_is_after_move(self):
        episode = {"confirm_date": SESSIONS[10]}
        self.assertEqual(
            evaluation.classify_detection(SESSIONS[20], episode, SESSIONS), "after_move")

    def test_observed_before_surge_is_before_move(self):
        episode = {"confirm_date": SESSIONS[20]}
        self.assertEqual(
            evaluation.classify_detection(SESSIONS[10], episode, SESSIONS), "before_move")

    def test_lead_time_counts_trading_days(self):
        self.assertEqual(evaluation.lead_time(SESSIONS[5], SESSIONS[15], SESSIONS), 10)

    def test_t02_after_move_excluded_from_lead_stats(self):
        records = [
            {"detection": "before_move", "lead_days": 10},
            {"detection": "before_move", "lead_days": 20},
            {"detection": "after_move", "lead_days": None},
        ]
        summary = evaluation.summarize_detection(records)
        self.assertEqual(summary["surge_episodes"], 3)
        self.assertEqual(summary["before_move_rate"], round(2 / 3, 4))
        self.assertEqual(summary["lead_days_median"], 20)


class OutcomeTest(unittest.TestCase):
    def test_t25_entry_is_first_session_after_report(self):
        self.assertEqual(evaluation.entry_date("20260701", SESSIONS), "20260702")

    def test_entry_none_when_report_is_after_last_session(self):
        self.assertIsNone(evaluation.entry_date("20261231", SESSIONS))

    def test_horizon_uses_first_session_on_or_after_target(self):
        daily = bars(SESSIONS)
        for date in SESSIONS:
            if date >= "20260729":
                for key in ("open", "high", "low", "close"):
                    daily[date][key] = 1100
        result = evaluation.evaluate_outcomes("20260702", SESSIONS, daily, horizons=(28,))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["horizons"][28]["status"], "done")
        self.assertAlmostEqual(result["horizons"][28]["return"], 0.1, places=6)

    def test_horizon_beyond_data_is_pending(self):
        daily = bars(SESSIONS)
        result = evaluation.evaluate_outcomes("20260702", SESSIONS, daily, horizons=(84,))
        self.assertEqual(result["horizons"][84]["status"], "pending")

    def test_drawdown_and_min_low_are_measured(self):
        daily = bars(SESSIONS)
        for key in ("open", "high", "low", "close"):
            daily["20260710"][key] = 800
        result = evaluation.evaluate_outcomes("20260702", SESSIONS, daily, horizons=(28,))
        horizon = result["horizons"][28]
        self.assertAlmostEqual(horizon["min_low"], -0.2, places=6)
        self.assertLess(horizon["close_mdd"], 0)

    def test_entry_on_suspended_bar_is_unavailable(self):
        daily = bars(SESSIONS)
        daily["20260702"]["close"] = 0
        self.assertEqual(
            evaluation.evaluate_outcomes("20260702", SESSIONS, daily)["status"],
            "entry_unavailable")


class BenchmarkTest(unittest.TestCase):
    def test_t43_market_cap_buckets(self):
        cases = [
            (50_000_000_000, "<1천억"),
            (200_000_000_000, "1~3천억"),
            (500_000_000_000, "3천억~1조"),
            (3_000_000_000_000, "1~5조"),
            (9_000_000_000_000, "5조+"),
            (None, "unknown"),
        ]
        for cap, expected in cases:
            self.assertEqual(evaluation.matched_benchmark_bucket(cap), expected)


if __name__ == "__main__":
    unittest.main()
