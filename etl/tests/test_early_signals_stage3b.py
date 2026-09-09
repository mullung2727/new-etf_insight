"""3단계 — 가격 시나리오 (PLAN §10.2).

T14 근거 없는 EPS/배수·Pr>=P0 → 계산 보류·unknown
T32 12개월 목표가와 8주 사건 → 목표가 기간 보존, 가격등급 cap
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _bootstrap  # noqa: F401,E402

from early_signals import pricing  # noqa: E402

CUTOFF = "2026-07-01"


def metric(name, after, period="2026", basis="연결", unit="원", comparable=True):
    return {"name": name, "period": period, "basis": basis, "before": None,
            "after": after, "unit": unit,
            "computed": {"comparable": comparable, "reason": "", "change_pct": None,
                         "direction": "new_value", "abs_change": None}}


def event(metrics, published="2026-06-20", group="g1", event_id="e1"):
    return {"event_id": event_id, "published_at": published + "T00:00:00+00:00",
            "origin_group_id": group, "source_version_id": "sv" + event_id,
            "change": {"metrics": metrics}}


class EarningsMultipleTest(unittest.TestCase):
    def test_eps_times_multiple_builds_scenario(self):
        events = [event([metric("EPS", 5000), metric("PER", 20, unit="배"),
                         metric("하방EPS", 4000)])]
        scenario = pricing.build_scenario(
            p0=80_000, price_as_of="20260701", events=events, cutoff_date=CUTOFF)
        self.assertEqual(scenario["method"], "earnings_multiple")
        self.assertEqual(scenario["conservative_price"], 100_000)
        self.assertEqual(scenario["risk_reference_price"], 80_000)

    def test_t14_metric_without_verified_comparability_is_ignored(self):
        """LLM 이 근거 없이 준 배수는 comparable=False 라 쓰지 않는다."""
        events = [event([metric("EPS", 5000), metric("PER", 20, unit="배", comparable=False)])]
        scenario = pricing.build_scenario(
            p0=80_000, price_as_of="20260701", events=events, cutoff_date=CUTOFF)
        self.assertEqual(scenario["method"], "unavailable")
        self.assertIn("scenario_unavailable", scenario["reason_codes"])

    def test_t14_missing_multiple_is_unavailable(self):
        scenario = pricing.build_scenario(
            p0=80_000, price_as_of="20260701", events=[event([metric("EPS", 5000)])],
            cutoff_date=CUTOFF)
        self.assertEqual(scenario["method"], "unavailable")

    def test_t14_negative_eps_is_rejected(self):
        events = [event([metric("EPS", -100), metric("PER", 20, unit="배")])]
        self.assertEqual(pricing.build_scenario(
            p0=80_000, price_as_of="20260701", events=events,
            cutoff_date=CUTOFF)["method"], "unavailable")


class BrokerReferenceTest(unittest.TestCase):
    def _two_brokers(self, a=120_000, b=110_000, published="2026-06-20"):
        return [event([metric("목표주가", a)], published=published, group="g1", event_id="e1"),
                event([metric("목표주가", b)], published=published, group="g2", event_id="e2")]

    def test_lowest_target_is_the_base(self):
        scenario = pricing.build_scenario(
            p0=100_000, price_as_of="20260701", events=self._two_brokers(),
            cutoff_date=CUTOFF, low_20d=90_000)
        self.assertEqual(scenario["method"], "broker_reference")
        self.assertEqual(scenario["base_reference_price"], 110_000)
        self.assertEqual(scenario["conservative_price"], 107_500)   # 100000 + 0.75*10000

    def test_single_broker_is_not_enough(self):
        events = [event([metric("목표주가", 120_000)])]
        self.assertEqual(pricing.build_scenario(
            p0=100_000, price_as_of="20260701", events=events,
            cutoff_date=CUTOFF)["method"], "unavailable")

    def test_same_origin_group_twice_is_one_broker(self):
        events = [event([metric("목표주가", 120_000)], group="g1", event_id="e1"),
                  event([metric("목표주가", 110_000)], group="g1", event_id="e2")]
        self.assertEqual(pricing.build_scenario(
            p0=100_000, price_as_of="20260701", events=events,
            cutoff_date=CUTOFF)["method"], "unavailable")

    def test_stale_target_beyond_45_days_is_excluded(self):
        old = self._two_brokers(published="2026-04-01")
        self.assertEqual(pricing.build_scenario(
            p0=100_000, price_as_of="20260701", events=old,
            cutoff_date=CUTOFF)["method"], "unavailable")

    def test_t32_horizon_is_preserved_not_converted(self):
        """12개월 목표가를 8~12주 목표수익으로 바꾸지 않는다."""
        scenario = pricing.build_scenario(
            p0=100_000, price_as_of="20260701", events=self._two_brokers(),
            cutoff_date=CUTOFF, low_20d=90_000)
        self.assertEqual(scenario["reference_horizon"], "12m_target")

    def test_t32_broker_method_caps_grade_at_medium(self):
        scenario = pricing.build_scenario(
            p0=100_000, price_as_of="20260701",
            events=self._two_brokers(a=200_000, b=190_000),
            cutoff_date=CUTOFF, low_20d=98_000)
        grade, reasons = pricing.grade_price(scenario)
        self.assertGreaterEqual(scenario["upside"], 0.20)      # high 조건은 넘지만
        self.assertEqual(grade, "medium")                      # 방법 상한에 걸린다
        self.assertIn("grade_capped_by_method", reasons)


class RiskPriceTest(unittest.TestCase):
    def test_20d_low_is_used_as_fallback_and_caps_grade(self):
        events = [event([metric("목표주가", 130_000)], group="g1", event_id="e1"),
                  event([metric("목표주가", 125_000)], group="g2", event_id="e2")]
        scenario = pricing.build_scenario(
            p0=100_000, price_as_of="20260701", events=events,
            cutoff_date=CUTOFF, low_20d=92_000)
        self.assertEqual(scenario["risk_reference_price"], 92_000)
        self.assertIn("risk_price_from_20d_low", scenario["reason_codes"])

    def test_t14_risk_price_above_current_yields_null_ratios(self):
        events = [event([metric("EPS", 5000), metric("PER", 20, unit="배"),
                         metric("하방EPS", 6000)])]      # 하방이 현재가보다 위
        scenario = pricing.build_scenario(
            p0=80_000, price_as_of="20260701", events=events, cutoff_date=CUTOFF)
        self.assertIsNone(scenario["upside"])
        self.assertIsNone(scenario["reward_risk"])
        self.assertIn("risk_price_ge_current", scenario["reason_codes"])
        self.assertEqual(pricing.grade_price(scenario)[0], "unknown")

    def test_no_price_is_unavailable(self):
        scenario = pricing.build_scenario(
            p0=None, price_as_of=None, events=[], cutoff_date=CUTOFF)
        self.assertEqual(scenario["method"], "unavailable")
        self.assertIn("no_current_price", scenario["reason_codes"])


class GradeTest(unittest.TestCase):
    def _scenario(self, upside, reward_risk, cap=None, horizon=None):
        return {"upside": upside, "reward_risk": reward_risk, "grade_cap": cap,
                "reference_horizon": horizon, "reason_codes": []}

    def test_high_needs_both_thresholds(self):
        self.assertEqual(pricing.grade_price(self._scenario(0.25, 2.5))[0], "high")
        self.assertEqual(pricing.grade_price(self._scenario(0.25, 1.8))[0], "medium")
        self.assertEqual(pricing.grade_price(self._scenario(0.15, 2.5))[0], "medium")

    def test_below_medium_is_low(self):
        grade, reasons = pricing.grade_price(self._scenario(0.05, 1.2))
        self.assertEqual(grade, "low")
        self.assertIn("upside_or_reward_risk_below_medium", reasons)

    def test_missing_ratio_is_unknown(self):
        self.assertEqual(pricing.grade_price(self._scenario(None, None))[0], "unknown")

    def test_12m_horizon_caps_high_to_medium(self):
        grade, reasons = pricing.grade_price(
            self._scenario(0.30, 3.0, horizon="12m_target"))
        self.assertEqual(grade, "medium")
        self.assertIn("grade_capped_by_horizon", reasons)


if __name__ == "__main__":
    unittest.main()
