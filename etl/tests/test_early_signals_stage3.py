"""3단계 수용 테스트 — 가격 계약·행동 결정 (PLAN §20-3).

T13 84일 밖 사건 · T15 휴장/수집실패 구분 · T16 권리락 보류 · T18 적격 0개
T37 4x4x4 전 조합 단일 행동 · T38 일봉 60/61/120·정지·quiet/extended 동시
T40 high 0건 원인 분해
"""
import itertools
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402

from early_signals import policy  # noqa: E402

SESSIONS = [f"2026-{m:02d}-{d:02d}" for m in (4, 5, 6) for d in range(1, 29)][:130]


def bars(sessions, close=1000, high=None, turnover=2_000_000_000, shares=1_000_000):
    return {d: {"date": d, "open": close, "high": high or close, "low": close,
                "close": close, "volume": 100, "trading_value": turnover,
                "market_cap": 100_000_000_000, "list_shrs": shares}
            for d in sessions}


def event(fact_type="observed_fact", expected_end="", condition="", source="sv1"):
    return {"source_version_id": source, "independence": "known",
            "change": {"fact_type": fact_type, "expected_end": expected_end,
                       "confirmation_condition": condition}}


class PriceHistoryTest(unittest.TestCase):
    def test_t38_needs_61_sessions_for_60d_return(self):
        daily = bars(SESSIONS)
        context = policy.compute_price_context("A", SESSIONS[:61], daily)
        self.assertTrue(context["history_ok"])
        self.assertIsNotNone(context["return_60d"])

    def test_t38_60_sessions_is_insufficient(self):
        result = policy.validate_price_history("A", SESSIONS[:60], bars(SESSIONS))
        self.assertFalse(result["ok"])
        self.assertIn("market_calendar_short", result["reason_codes"])

    def test_t38_missing_session_is_history_gap(self):
        daily = bars(SESSIONS)
        del daily[SESSIONS[56]]      # 61개 창 안
        result = policy.validate_price_history("A", SESSIONS[:61], daily)
        self.assertIn("history_gap", result["reason_codes"])

    def test_t15_suspended_bar_is_not_treated_as_valid(self):
        daily = bars(SESSIONS)
        daily[SESSIONS[58]]["close"] = 0      # 61개 창 안
        result = policy.validate_price_history("A", SESSIONS[:61], daily)
        self.assertIn("suspended_or_invalid_bar", result["reason_codes"])

    def test_zero_turnover_alone_is_not_suspension(self):
        daily = bars(SESSIONS, turnover=0)
        result = policy.validate_price_history("A", SESSIONS[:61], daily)
        self.assertNotIn("suspended_or_invalid_bar", result["reason_codes"])

    def test_t16_share_count_jump_is_adjustment_pending(self):
        daily = bars(SESSIONS)
        for date in SESSIONS[40:]:
            daily[date]["list_shrs"] = 5_000_000      # 5배 → 밴드 밖
        result = policy.validate_price_history("A", SESSIONS[:61], daily)
        self.assertIn("adjustment_pending", result["reason_codes"])

    def test_t38_120d_high_is_auxiliary_only(self):
        daily = bars(SESSIONS[:61])
        context = policy.compute_price_context("A", SESSIONS[:61], daily)
        self.assertIsNone(context["drawdown_120d"])
        self.assertIn("insufficient_120d", context["reason_codes"])
        self.assertTrue(context["history_ok"])      # 보조 결측은 승격을 막지 않는다


class PriceStateTest(unittest.TestCase):
    def test_t38_extended_wins_when_both_match(self):
        """20일 <10% 이면서 5일 >=15% 면 extended 다."""
        state = policy.classify_price_state(
            {"return_5d": 0.16, "return_20d": 0.05, "turnover_ratio": 1.0})
        self.assertEqual(state, "extended")

    def test_quiet_requires_both_conditions(self):
        self.assertEqual(policy.classify_price_state(
            {"return_5d": 0.01, "return_20d": 0.05, "turnover_ratio": 1.0}), "quiet")
        self.assertEqual(policy.classify_price_state(
            {"return_5d": 0.01, "return_20d": 0.05, "turnover_ratio": 3.0}), "moving")

    def test_missing_required_metric_is_unknown(self):
        self.assertEqual(policy.classify_price_state(
            {"return_5d": None, "return_20d": 0.05, "turnover_ratio": 1.0}), "unknown")


class GradeTest(unittest.TestCase):
    def test_evidence_high_needs_two_independent_groups_and_a_fact(self):
        events = [event(source="sv1"), event(source="sv2")]
        grade, _ = policy.grade_evidence(events, {"sv1": "g1", "sv2": "g2"})
        self.assertEqual(grade, "high")

    def test_t40_same_group_twice_is_not_two_sources(self):
        events = [event(source="sv1"), event(source="sv2")]
        grade, reasons = policy.grade_evidence(events, {"sv1": "g1", "sv2": "g1"})
        self.assertEqual(grade, "medium")
        self.assertIn("independent_sources_lt_2", reasons)

    def test_t40_estimates_without_fact_stay_medium(self):
        events = [event(fact_type="analyst_estimate", source="sv1"),
                  event(fact_type="analyst_estimate", source="sv2")]
        grade, reasons = policy.grade_evidence(events, {"sv1": "g1", "sv2": "g2"})
        self.assertEqual(grade, "medium")
        self.assertIn("no_observed_fact", reasons)

    def test_rumor_only_is_low(self):
        grade, _ = policy.grade_evidence([event(fact_type="rumor")], {"sv1": "g1"})
        self.assertEqual(grade, "low")

    def test_t13_event_beyond_84_days_is_low(self):
        grade, reasons = policy.grade_timing([event(expected_end="2027-06-30")], "2026-07-01")
        self.assertEqual(grade, "low")
        self.assertIn("expected_beyond_84d", reasons)

    def test_timing_high_needs_confirmation_condition(self):
        within = [event(expected_end="2026-08-15", condition="3분기 양산 개시 공시")]
        self.assertEqual(policy.grade_timing(within, "2026-07-01")[0], "high")
        without = [event(expected_end="2026-08-15")]
        self.assertEqual(policy.grade_timing(without, "2026-07-01")[0], "medium")

    def test_no_date_is_unknown(self):
        self.assertEqual(policy.grade_timing([event()], "2026-07-01")[0], "unknown")


class PolicyTest(unittest.TestCase):
    OK = {"history_ok": True, "turnover_20d": 5_000_000_000, "reason_codes": [], "state": "moving"}

    def test_t37_every_grade_combination_returns_exactly_one_action(self):
        actions = set()
        for evidence, timing, price, new in itertools.product(
                policy.GRADES, policy.GRADES, policy.GRADES, (True, False)):
            result = policy.apply_policy(
                evidence=evidence, timing=timing, price=price, has_new_change=new,
                price_context=dict(self.OK), has_open_condition=new)
            self.assertIn("action", result)
            self.assertIsInstance(result["reason_codes"], list)
            self.assertTrue(result["reason_codes"] or result["action"] == "review_buy")
            actions.add(result["action"])
        self.assertEqual(len(list(itertools.product(policy.GRADES, policy.GRADES,
                                                    policy.GRADES, (True, False)))), 128)
        self.assertTrue({"review_buy", "watch"} <= actions)

    def test_t37_review_buy_requires_all_three(self):
        result = policy.apply_policy(evidence="high", timing="high", price="medium",
                                     has_new_change=True, price_context=dict(self.OK))
        self.assertEqual(result["action"], "review_buy")

    def test_t37_price_high_does_not_promote_medium_evidence(self):
        result = policy.apply_policy(evidence="medium", timing="high", price="high",
                                     has_new_change=True, price_context=dict(self.OK))
        self.assertEqual(result["action"], "watch")
        self.assertIn("evidence_confirmation_pending", result["reason_codes"])

    def test_t37_invalidation_beats_price_gap(self):
        broken = {"history_ok": False, "turnover_20d": 0, "reason_codes": ["history_gap"]}
        result = policy.apply_policy(evidence="high", timing="high", price="high",
                                     has_new_change=True, price_context=broken,
                                     invalidated=True)
        self.assertEqual(result["action"], "invalidated")

    def test_price_history_gap_blocks_promotion(self):
        broken = {"history_ok": False, "turnover_20d": 9e9, "reason_codes": ["history_gap"]}
        result = policy.apply_policy(evidence="high", timing="high", price="high",
                                     has_new_change=True, price_context=broken)
        self.assertEqual(result["action"], "watch")
        self.assertIn("price_history_incomplete", result["reason_codes"])

    def test_liquidity_below_min_blocks_promotion(self):
        thin = {"history_ok": True, "turnover_20d": 500_000_000, "reason_codes": []}
        result = policy.apply_policy(evidence="high", timing="high", price="high",
                                     has_new_change=True, price_context=thin)
        self.assertEqual(result["action"], "watch")
        self.assertIn("liquidity_below_min", result["reason_codes"])

    def test_wait_price_needs_price_low_not_unknown(self):
        low = policy.apply_policy(evidence="high", timing="high", price="low",
                                  has_new_change=True, price_context=dict(self.OK))
        self.assertEqual(low["action"], "wait_price")
        unknown = policy.apply_policy(evidence="high", timing="high", price="unknown",
                                      has_new_change=True, price_context=dict(self.OK))
        self.assertEqual(unknown["action"], "watch")
        self.assertIn("price_unavailable", unknown["reason_codes"])

    def test_repeated_only_without_open_condition_is_inactive(self):
        result = policy.apply_policy(evidence="low", timing="unknown", price="unknown",
                                     has_new_change=False, price_context=dict(self.OK),
                                     has_open_condition=False)
        self.assertEqual(result["action"], "inactive")


class SelectionTest(unittest.TestCase):
    def _item(self, code, theme, price="medium"):
        return {"subject_id": code, "action": "review_buy", "primary_theme": theme,
                "grades": {"price": price}, "next_check_date": "2026-08-01"}

    def test_t31_caps_total_and_per_theme(self):
        items = [self._item(f"00000{i}", "T1") for i in range(8)]
        picked = policy.select_candidates(items)
        self.assertEqual(len(picked), 2)      # 한 테마 최대 2개

    def test_t18_no_review_buy_yields_empty(self):
        self.assertEqual(policy.select_candidates(
            [{"subject_id": "a", "action": "watch", "grades": {"price": "high"}}]), [])

    def test_price_high_is_ranked_first(self):
        items = [self._item("000002", "A", "medium"), self._item("000001", "B", "high")]
        self.assertEqual(policy.select_candidates(items)[0]["subject_id"], "000001")


if __name__ == "__main__":
    unittest.main()


class MarketCalendarTest(unittest.TestCase):
    """§9.1 — 수집이 덜 된 날을 거래일로 세면 멀쩡한 종목이 history_gap 이 된다."""

    class _Con:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, sql, params=None):
            return self

        def fetchall(self):
            return self.rows

    def test_sparse_day_is_dropped_from_calendar(self):
        rows = [(f"2026040{i}", 2700) for i in range(1, 8)]
        rows.insert(3, ("20260417", 604))      # 수집 결손일
        calendar = policy.market_calendar("20260701", self._Con(rows))
        self.assertNotIn("20260417", calendar["sessions"])
        self.assertEqual([s["date"] for s in calendar["sparse_sessions"]], ["20260417"])
        self.assertEqual(calendar["median_listings"], 2700)

    def test_normal_variation_is_kept(self):
        rows = [("20260401", 2700), ("20260402", 2508), ("20260403", 2766)]
        calendar = policy.market_calendar("20260701", self._Con(rows))
        self.assertEqual(len(calendar["sessions"]), 3)
        self.assertEqual(calendar["sparse_sessions"], [])

    def test_empty_source_is_safe(self):
        calendar = policy.market_calendar("20260701", self._Con([]))
        self.assertEqual(calendar["sessions"], [])
