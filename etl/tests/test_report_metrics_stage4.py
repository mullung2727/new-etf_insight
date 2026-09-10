"""Stage 4 — R4 추정표 파싱 (PLAN §4 Stage 4).

실측 fixture 3종이 두 방향을 모두 덮는다:
  신한 = 연도가 행(지표가 헤더), 유진·유안타 = 지표가 행(연도가 헤더).
"""
import unittest
from pathlib import Path

from scripts.report_metrics.parse import parse_estimates

FIXTURES = Path(__file__).parent / "fixtures" / "report_pages"


def _fx(name: str) -> str:
    return (FIXTURES / f"{name}_p1.txt").read_text(encoding="utf-8")


def _by_year(estimates):
    return {e.fiscal_year: e for e in estimates}


class RealFixtures(unittest.TestCase):
    def test_shinhan_year_rows_with_column_units(self):
        got = _by_year(parse_estimates(_fx("shinhan"), report_year=2026))
        self.assertEqual(sorted(got), [2024, 2025, 2026, 2027, 2028])
        self.assertFalse(got[2024].is_forecast)
        self.assertTrue(got[2026].is_forecast)
        y26 = got[2026]
        self.assertAlmostEqual(y26.revenue, 11720.0)          # 1,172.0 십억원 → 억원
        self.assertAlmostEqual(y26.operating_profit, 867.0)
        self.assertAlmostEqual(y26.net_profit, 404.0)         # 지배순이익
        self.assertAlmostEqual(y26.per, 5.1)
        self.assertAlmostEqual(y26.roe, 8.6)
        self.assertAlmostEqual(y26.pbr, 0.4)
        self.assertAlmostEqual(y26.div_yield, 8.2)
        self.assertAlmostEqual(got[2027].revenue, 11443.0)

    def test_eugene_metric_rows(self):
        got = _by_year(parse_estimates(_fx("eugene"), report_year=2026))
        self.assertEqual(sorted(got), [2025, 2026, 2027])
        self.assertFalse(got[2025].is_forecast)               # 2025A
        self.assertTrue(got[2026].is_forecast)                # 2026E
        y26 = got[2026]
        self.assertAlmostEqual(y26.revenue, 12349.0)          # (십 억 원) 공백 섞인 단위
        self.assertAlmostEqual(y26.net_profit, 572.0)         # 당기순이익
        self.assertAlmostEqual(y26.eps, 1263.0)               # 주당 값은 원 그대로
        self.assertAlmostEqual(y26.roe, 12.9)
        self.assertAlmostEqual(y26.pbr, 0.5)

    def test_yuanta_unit_on_line_above_and_controlling_profit(self):
        got = _by_year(parse_estimates(_fx("yuanta_notrated"), report_year=2026))
        self.assertEqual(sorted(got), [2022, 2023, 2024, 2025])
        self.assertTrue(all(not e.is_forecast for e in got.values()))
        self.assertAlmostEqual(got[2022].revenue, 4866.0)     # (억원, 원, %, 배)
        self.assertAlmostEqual(got[2022].net_profit, 822.0)
        self.assertAlmostEqual(got[2025].roe, 6.8)


class Synthetic(unittest.TestCase):
    def test_missing_unit_keeps_row_but_nulls_amounts(self):
        text = "결산 2025A 2026F\n매출액 100 120\nROE 10.0 11.0\n"
        got = _by_year(parse_estimates(text))
        self.assertIsNone(got[2026].revenue)
        self.assertAlmostEqual(got[2026].roe, 11.0)

    def test_paren_negative_and_placeholders(self):
        text = "(억원) 2025A 2026F 2027F\n영업이익 (38) - 1,200\nEPS(원) -1,354 적지 542\n"
        got = _by_year(parse_estimates(text))
        self.assertAlmostEqual(got[2025].operating_profit, -38.0)
        self.assertAlmostEqual(got[2027].operating_profit, 1200.0)
        self.assertAlmostEqual(got[2025].eps, -1354.0)
        # 빈 셀은 자리만 차지한다(열 정렬 유지). 값이 하나도 없는 2026 은 버린다.
        self.assertNotIn(2026, got)

    def test_no_table_returns_empty(self):
        self.assertEqual(parse_estimates("본문만 있다. 2026년 매출 성장 전망.\n" * 20), [])

    def test_quarter_headers_are_not_year_tables(self):
        text = "(억원) 1Q26 2Q26 3Q26F\n매출액 100 110 120\n"
        self.assertEqual(parse_estimates(text), [])

    def test_dates_are_not_year_headers(self):
        text = "(억원) 2026.08.18 2026.08.19\n매출액 100 110\n"
        self.assertEqual(parse_estimates(text), [])

    def test_later_table_fills_only_missing_fields(self):
        text = ("(억원) 2025A 2026F\n매출액 100 120\n"
                "\n" * 40 +
                "(억원) 2025A 2026F\n매출액 999 999\nROE(%) 10.0 11.0\n")
        got = _by_year(parse_estimates(text))
        self.assertAlmostEqual(got[2026].revenue, 120.0)      # 첫 표 우선
        self.assertAlmostEqual(got[2026].roe, 11.0)           # 빈 칸은 뒤 표가 채움

    def test_non_controlling_is_not_controlling_profit(self):
        text = "(억원) 2025A 2026F\n비지배순이익 5 6\n지배순이익 100 120\n"
        got = _by_year(parse_estimates(text))
        self.assertAlmostEqual(got[2026].net_profit, 120.0)

    def test_suffixless_years_use_report_year(self):
        text = "(억원) 2025 2026 2027\n매출액 1 2 3\n"
        got = _by_year(parse_estimates(text, report_year=2026))
        self.assertFalse(got[2025].is_forecast)
        self.assertTrue(got[2026].is_forecast)


if __name__ == "__main__":
    unittest.main()
