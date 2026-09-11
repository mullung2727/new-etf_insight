"""Stage 5 — R5 RIM 준비 (PLAN §4 Stage 5)."""
import unittest

from scripts.report_metrics.models import YearEstimate
from scripts.report_metrics.rim import build_rim_inputs, rim_value


def _est(year, forecast, np_, roe):
    return YearEstimate(fiscal_year=year, is_forecast=forecast, net_profit=np_, roe=roe)


BASIC = [_est(2025, False, 100.0, 10.0), _est(2026, True, 120.0, 11.0), _est(2027, True, 130.0, 11.0)]


class BuildInputs(unittest.TestCase):
    def test_equity_backed_out_from_profit_and_roe(self):
        inputs = build_rim_inputs(BASIC)
        self.assertAlmostEqual(inputs.base_equity, 1000.0)            # 100 / 10%
        self.assertEqual(inputs.base_year, 2025)
        self.assertEqual(inputs.years, [2026, 2027])
        self.assertAlmostEqual(inputs.equities[0], 120.0 / 0.11)

    def test_year_without_roe_is_skipped_with_warning(self):
        est = BASIC + [_est(2028, True, 140.0, None), _est(2029, True, 150.0, 0.0)]
        inputs = build_rim_inputs(est)
        self.assertEqual(inputs.years, [2026, 2027])
        self.assertEqual(len(inputs.warnings), 2)

    def test_middle_gap_stops_the_series(self):
        """2027 이 비면 2028 을 2026 자본에 붙여 계산하면 안 된다(B_{t-1} 연쇄가 끊김)."""
        est = BASIC[:2] + [_est(2027, True, 130.0, None), _est(2028, True, 140.0, 12.0)]
        inputs = build_rim_inputs(est)
        self.assertEqual(inputs.years, [2026])
        self.assertEqual(len(inputs.warnings), 2)

    def test_no_forecast_years_returns_none(self):
        self.assertIsNone(build_rim_inputs([_est(2024, False, 90.0, 9.0), _est(2025, False, 100.0, 10.0)]))
        self.assertIsNone(rim_value(None))

    def test_missing_base_year_is_approximated_with_warning(self):
        inputs = build_rim_inputs(BASIC[1:])
        self.assertAlmostEqual(inputs.base_equity, 120.0 / 0.11 - 120.0)
        self.assertTrue(any("근사" in w for w in inputs.warnings))


class RimValue(unittest.TestCase):
    def test_hand_calculation(self):
        # RI1=(0.11-0.08)*1000=30, B1=1090.9091, RI2=0.03*1090.9091=32.7273
        # V = 1000 + 30/1.08 + 32.7273/1.08^2 + 32.7273*0.8/0.28/1.08^2 = 1136.0029
        got = rim_value(build_rim_inputs(BASIC))
        self.assertAlmostEqual(got["equity_value"], 1136.0029, places=4)
        self.assertEqual(got["params"], {"r": 0.08, "omega": 0.8, "base_year": 2025,
                                         "years": [2026, 2027]})

    def test_higher_discount_rate_lowers_value(self):
        values = [rim_value(build_rim_inputs(BASIC, r=r))["equity_value"] for r in (0.06, 0.08, 0.10)]
        self.assertGreater(values[0], values[1])
        self.assertGreater(values[1], values[2])

    def test_per_share_value_when_shares_given(self):
        got = rim_value(build_rim_inputs(BASIC), shares=10_000_000)
        self.assertAlmostEqual(got["value_per_share"], got["equity_value"] * 1e8 / 10_000_000)
        self.assertIsNone(rim_value(build_rim_inputs(BASIC))["value_per_share"])


if __name__ == "__main__":
    unittest.main()
