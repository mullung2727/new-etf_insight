import unittest

from new_etf_insight.etf_classifier import classify_pre_listing_equity_etf
from new_etf_insight.filing_filter import is_candidate_filing, matches_candidate_query
from new_etf_insight.pdf_finder import extract_dcm_no, extract_pdf_links, pick_primary_pdf_link
from new_etf_insight.sample_summary import summarize_hyundai_physical_ai


TARGET_TEXT = """
1. 집합투자기구 명칭: KB RISE 현대차고정피지컬AI 증권 상장지수 투자신탁(주식)(ET942)
2. 집합투자업자 명칭: KB자산운용주식회사
5. 증권신고서 효력발생일: 2026년 04월 29일
상장일: 2026년 00월 00일(예정)
이 투자신탁은 국내주식을 법에서 정하는 주된 투자대상으로 하며,
한국경제신문에서 산출 및 관리하는 “KEDI 현대차고정피지컬AI 지수(시장가격)”를 기초지수로 한다.
증권(주식형), 개방형, 추가형, 상장지수투자신탁(ETF)
기초지수의 구성종목과 구성비율을 완전 복제하는 방식으로 포트폴리오를 구성할 계획입니다.
투자신탁보수 합계 0.400
출처: 한국경제신문, 2026년 03월 31일 종가 기준
구분 종목명 비율% 구분 종목명 비율%
1 현대차 26.48 9 로보티즈 3.01
2 기아 16.97 10 에스엘 2.19
3 현대모비스 15.01 11 HL만도 1.96
4 레인보우로보틱스 8.61 12 고영 1.43
5 현대오토에버 6.42 13 휴림로봇 1.05
6 LG이노텍 5.92 14 클로봇 1.03
7 LG씨엔에스 4.61 15 하이젠알앤엠 0.92
8 두산로보틱스 4.39 -
"""


class PipelineModulesTest(unittest.TestCase):
    def test_candidate_filter_keeps_investment_prospectus_and_excludes_etn(self) -> None:
        self.assertTrue(
            is_candidate_filing(
                {
                    "corp_name": "KB자산운용",
                    "report_nm": "투자설명서(집합투자증권)(KBRISE현대차고정피지컬AI증권상장지수투자신탁(주식))",
                }
            )
        )

    def test_candidate_query_matches_without_spaces(self) -> None:
        self.assertTrue(
            matches_candidate_query(
                {
                    "corp_name": "KB자산운용",
                    "report_nm": "투자설명서(집합투자증권)(KBRISE현대차고정피지컬AI증권상장지수투자신탁(주식))",
                },
                "KB RISE 현대차고정피지컬AI",
            )
        )
        self.assertFalse(
            is_candidate_filing(
                {
                    "corp_name": "한국투자증권",
                    "report_nm": "일괄신고추가서류(파생결합증권-상장지수증권)",
                }
            )
        )

    def test_pdf_finder_extracts_and_picks_primary_pdf_link(self) -> None:
        html = """
        <a class="btnFile" href="/pdf/download/pdf.do?rcp_no=20260429000010&dcm_no=11351971"></a>
        <a class="btnFile" href="/pdf/download/file.do?rcp_no=20260429000010&dcm_id=10611&dcm_seq=815&fl_nm=sample.pdf"></a>
        """

        links = extract_pdf_links(html, "https://dart.fss.or.kr")

        self.assertEqual(len(links), 2)
        self.assertEqual(
            pick_primary_pdf_link(links),
            "https://dart.fss.or.kr/pdf/download/file.do?rcp_no=20260429000010&dcm_id=10611&dcm_seq=815&fl_nm=sample.pdf",
        )

    def test_pdf_finder_extracts_primary_dcm_no(self) -> None:
        html = """
        node1['dcmNo'] = "11351971";
        <button onclick="openPdfDownload('20260429000010', '11351971');">다운로드</button>
        """

        self.assertEqual(extract_dcm_no(html), "11351971")

    def test_classifier_detects_pre_listing_equity_etf(self) -> None:
        result = classify_pre_listing_equity_etf(TARGET_TEXT)

        self.assertTrue(result.is_pre_listing_equity_etf)
        self.assertIn("상장지수", result.reasons)
        self.assertIn("주식형", result.reasons)
        self.assertIn("상장예정", result.reasons)

    def test_sample_summary_extracts_hardcoded_target_fields(self) -> None:
        summary = summarize_hyundai_physical_ai(
            TARGET_TEXT,
            source={
                "rcept_no": "20260429000010",
                "dart_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260429000010",
                "pdf_url": "https://dart.fss.or.kr/pdf/download/file.do?sample.pdf",
            },
        )

        self.assertEqual(summary["etf_name"], "KB RISE 현대차고정피지컬AI 증권 상장지수 투자신탁(주식)")
        self.assertEqual(summary["fund_code"], "ET942")
        self.assertEqual(summary["manager"], "KB자산운용주식회사")
        self.assertEqual(summary["effective_date"], "2026-04-29")
        self.assertIsNone(summary["expected_listing_date"])
        self.assertEqual(summary["underlying_index"], "KEDI 현대차고정피지컬AI 지수(시장가격)")
        self.assertEqual(summary["total_fee"], 0.4)
        self.assertEqual(summary["portfolio_example_as_of"], "2026-03-31")
        self.assertEqual(len(summary["portfolio_example"]), 15)
        self.assertEqual(summary["portfolio_example"][0], {"name": "현대차", "weight": 26.48})
        self.assertTrue(summary["is_pre_listing_equity_etf"])


if __name__ == "__main__":
    unittest.main()
