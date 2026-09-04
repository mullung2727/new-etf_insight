import unittest

from scripts.build_financial_indicators import parse_report_period


class ParseReportPeriodTest(unittest.TestCase):
    def test_half_year_and_annual_use_report_name(self):
        self.assertEqual(parse_report_period("반기보고서 (2026.06)"), ("2026", "11012", "06"))
        self.assertEqual(parse_report_period("사업보고서 (2025.12)"), ("2025", "11011", "12"))

    def test_correction_prefix_is_stripped(self):
        self.assertEqual(parse_report_period("[기재정정]반기보고서 (2026.06)"), ("2026", "11012", "06"))
        self.assertEqual(parse_report_period("[첨부추가]사업보고서 (2025.12)"), ("2025", "11011", "12"))

    def test_quarter_split_by_settlement_month(self):
        self.assertEqual(parse_report_period("분기보고서 (2026.03)"), ("2026", "11013", "03"))
        self.assertEqual(parse_report_period("분기보고서 (2026.09)"), ("2026", "11014", "09"))

    def test_non_december_year_end_is_kept_for_named_reports(self):
        # DART bsns_year는 결산기가 끝나는 연도다. 기신정기(3월 결산)의 2024 사업보고서가
        # bsns_year=2024 / stlm_dt=2024-03-31로 실제 적재돼 있다. 사업·반기는 이름만으로
        # 보고서 종류가 정해지므로 결산월을 12·06으로 좁히면 이런 회사가 통째로 빠진다.
        self.assertEqual(parse_report_period("사업보고서 (2024.03)"), ("2024", "11011", "03"))
        self.assertEqual(parse_report_period("반기보고서 (2026.03)"), ("2026", "11012", "03"))

    def test_ambiguous_quarter_is_dropped(self):
        # 12월 결산이 아닌 법인의 분기보고서 — 1분기·3분기를 가를 수 없어 스킵한다.
        self.assertIsNone(parse_report_period("분기보고서 (2026.06)"))

    def test_non_periodic_filings_are_dropped(self):
        self.assertIsNone(parse_report_period("사업보고서제출기한연장신고서"))
        self.assertIsNone(parse_report_period("소액공모법인결산서류등"))
        self.assertIsNone(parse_report_period(""))
