import unittest

from new_etf_insight.etf_classifier import classify_pre_listing_equity_etf
from new_etf_insight.filing_filter import is_candidate_filing, matches_candidate_query




TARGET_TEXT = """
1. 집합투자기구 명칭: KB RISE 현대차고정피지컬AI 증권 상장지수 투자신탁(주식)(ET942)
2. 집합투자업자 명칭: KB자산운용주식회사
5. 증권신고서 효력발생일: 2026년 04월 29일
상장일: 2026년 00월 00일(예정)
이 투자신탁은 국내주식을 법에서 정하는 주된 투자대상으로 하며,
한국경제신문에서 산출 및 관리하는 "KEDI 현대차고정피지컬AI 지수(시장가격)"를 기초지수로 한다.
증권(주식형), 개방형, 추가형, 상장지수투자신탁(ETF)
기초지수의 구성종목과 구성비율을 완전 복제하는 방식으로 포트폴리오를 구성할 계획입니다.
투자신탁보수 합계 0.400
"""


class FilingFilterTest(unittest.TestCase):
    def test_keeps_investment_prospectus(self) -> None:
        self.assertTrue(
            is_candidate_filing(
                {
                    "corp_name": "KB자산운용",
                    "report_nm": "투자설명서(집합투자증권)(KBRISE현대차고정피지컬AI증권상장지수투자신탁(주식))",
                }
            )
        )

    def test_excludes_etn(self) -> None:
        self.assertFalse(
            is_candidate_filing(
                {
                    "corp_name": "한국투자증권",
                    "report_nm": "일괄신고추가서류(파생결합증권-상장지수증권)",
                }
            )
        )

    def test_excludes_non_equity_etf(self) -> None:
        self.assertFalse(
            is_candidate_filing(
                {
                    "corp_name": "삼성자산운용",
                    "report_nm": "투자설명서(집합투자증권-KODEX 국채10년 상장지수투자신탁(채권))",
                }
            )
        )

    def test_query_matches_without_spaces(self) -> None:
        self.assertTrue(
            matches_candidate_query(
                {
                    "corp_name": "KB자산운용",
                    "report_nm": "투자설명서(집합투자증권)(KBRISE현대차고정피지컬AI증권상장지수투자신탁(주식))",
                },
                "KB RISE 현대차고정피지컬AI",
            )
        )


class EtfClassifierTest(unittest.TestCase):
    def test_detects_pre_listing_equity_etf(self) -> None:
        result = classify_pre_listing_equity_etf(TARGET_TEXT)

        self.assertTrue(result.is_pre_listing_equity_etf)
        self.assertIn("상장지수", result.reasons)
        self.assertIn("주식형", result.reasons)
        self.assertIn("상장예정", result.reasons)


if __name__ == "__main__":
    unittest.main()
