import unittest

from scripts.dart_recent_filings import candidate_reason, matches_query, resolve_date_range


class DartRecentFilingsTest(unittest.TestCase):
    def test_resolve_date_range_uses_explicit_dates(self) -> None:
        self.assertEqual(resolve_date_range(days=7, begin="20260429", end="20260429"), ("20260429", "20260429"))

    def test_matches_query_checks_report_and_corp_fields(self) -> None:
        filing = {
            "corp_name": "케이비자산운용",
            "report_nm": "증권신고서(집합투자증권-KB RISE 현대차고정피지컬AI)",
        }

        self.assertTrue(matches_query(filing, "현대차고정피지컬AI"))

    def test_candidate_reason_excludes_etn_disclosures(self) -> None:
        filing = {"report_nm": "일괄신고추가서류(파생결합증권-상장지수증권)"}

        self.assertIsNone(candidate_reason(filing))


if __name__ == "__main__":
    unittest.main()
