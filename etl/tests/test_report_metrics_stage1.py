"""Stage 1 — 경로/헤더 파싱 (PLAN §4 Stage 1).

fixture 는 실제 PDF 1페이지에서 뽑아 고정한 텍스트(tests/fixtures/report_pages/*.txt).
네트워크·PDF 의존 없이 결정적으로 돈다.
"""
import unittest
from pathlib import Path

from scripts.report_metrics.models import STATUS_IMAGE_PDF, STATUS_NO_TARGET, STATUS_OK
from scripts.report_metrics.parse import (
    MIN_TEXT_CHARS,
    classify_status,
    parse_header,
    parse_path,
)

FIXTURES = Path(__file__).parent / "fixtures" / "report_pages"


def _fx(name: str) -> str:
    return (FIXTURES / f"{name}_p1.txt").read_text(encoding="utf-8")


class ParsePath(unittest.TestCase):
    def test_normal_path(self):
        p = "exports/stock_reports/AJ네트웍스_095570/2026-08-18_신한투자증권_20260818_company_805771000.pdf"
        got = parse_path(p)
        self.assertEqual(got["stock_code"], "095570")
        self.assertEqual(got["stock_name"], "AJ네트웍스")
        self.assertEqual(got["broker"], "신한투자증권")
        self.assertEqual(got["report_date"], "2026-08-18")
        self.assertEqual(got["pdf_key"], "20260818_company_805771000")

    def test_stock_name_with_underscore(self):
        """sanitize 가 '/' 를 '_' 로 바꾸므로 종목명에 '_' 가 들어올 수 있다 → rsplit."""
        p = "x/CJ_CGV_079160/2026-07-01_대신증권_20260701_company_1.pdf"
        got = parse_path(p)
        self.assertEqual(got["stock_code"], "079160")
        self.assertEqual(got["stock_name"], "CJ_CGV")

    def test_broker_with_underscore_keeps_key_consistent(self):
        """증권사명에 '_' 가 있어도 key 는 stem 의 꼬리와 일치해야 한다 (PLAN §2.3)."""
        p = "x/삼성전자_005930/2026-07-01_굿_모닝증권_20260701_company_9.pdf"
        got = parse_path(p)
        stem = Path(p).stem
        self.assertTrue(stem.endswith(got["pdf_key"]))
        self.assertEqual(f"{got['report_date']}_{got['broker']}_{got['pdf_key']}", stem)


class ParseHeader(unittest.TestCase):
    def test_shinhan(self):
        got = parse_header(_fx("shinhan"), "2026-08-18")
        self.assertEqual(got["target_price"], 6500)
        self.assertEqual(got["price_at_report"], 4520)
        self.assertEqual(got["price_at_report_date"], "2026-08-14")
        self.assertEqual(got["opinion"], "매수")
        self.assertAlmostEqual(got["upside_printed"], 0.438, places=4)

    def test_eugene(self):
        got = parse_header(_fx("eugene"), "2026-08-18")
        self.assertEqual(got["target_price"], 7000)
        self.assertEqual(got["price_at_report"], 4520)
        self.assertEqual(got["price_at_report_date"], "2026-08-14")
        self.assertEqual(got["opinion"], "BUY")
        # '현재 직전 변동' 표에서 직전 목표주가를 읽는다
        self.assertEqual(got["prev_target_printed"], 7000)

    def test_yuanta_not_rated(self):
        got = parse_header(_fx("yuanta_notrated"), "2026-08-10")
        self.assertIsNone(got["target_price"])
        self.assertIsNone(got["prev_target_printed"])
        self.assertEqual(got["price_at_report"], 19660)
        self.assertEqual(got["price_at_report_date"], "2026-08-07")
        self.assertEqual(got["opinion"], "NOT RATED")
        self.assertIsNone(got["upside_printed"])

    def test_prev_line_is_not_mistaken_for_target(self):
        """'직전 목표주가' 줄이 목표주가로 잡히면 안 된다 (부정 요구)."""
        text = "목표주가 9,000원\n직전 목표주가 8,000원\n현재주가 (7/1) 5,000원\n" + "x" * 300
        got = parse_header(text, "2026-07-02")
        self.assertEqual(got["target_price"], 9000)
        self.assertEqual(got["prev_target_printed"], 8000)

    def test_year_rollback_when_month_ahead_of_report(self):
        """1월 리포트에 '(12/28)' → 전년으로 보정."""
        text = "목표주가 1,000원\n현재주가 (12/28) 800원\n" + "x" * 300
        got = parse_header(text, "2027-01-05")
        self.assertEqual(got["price_at_report_date"], "2026-12-28")

    def test_tp_abbreviation(self):
        """교보 양식: 'TP 405,000원 하향'."""
        got = parse_header("TP 405,000원  하향\n현재주가 (8/14) 300,000원\n" + "x" * 300, "2026-08-18")
        self.assertEqual(got["target_price"], 405000)

    def test_spaced_target_label_does_not_take_horizon_number(self):
        """iM 양식: '목표주 가(12M) 57,000원' — 12 를 목표가로 잡으면 안 된다."""
        got = parse_header("목표주 가(12M) 57,000원(유지)\n종가(2026.08.14) 48,300원\n" + "x" * 300,
                           "2026-08-18")
        self.assertEqual(got["target_price"], 57000)
        self.assertEqual(got["price_at_report"], 48300)
        self.assertEqual(got["price_at_report_date"], "2026-08-14")

    def test_price_fallback_line_kiwoom(self):
        """키움 양식: 현재주가 줄 없이 '주가(7/1): 24,800원'."""
        got = parse_header("Not Rated\n주가(7/1): 24,800원\n" + "x" * 300, "2026-07-02")
        self.assertEqual(got["price_at_report"], 24800)
        self.assertEqual(got["price_at_report_date"], "2026-07-01")
        self.assertEqual(got["opinion"], "NOT RATED")

    def test_daishin_split_lines(self):
        """대신 양식: '현재주가' / '(26.06.29)' / '41,400' 이 각각 다른 줄.
        본문 '현재 주가는 2026F PER' 의 2026 을 주가로 잡으면 안 된다(덴티움 실측)."""
        text = ("6개월 목표주가 78,000\n유지\n현재주가\n(26.06.29)\n41,400\n의료 장비 및 서비스 업종\n"
                "현재 주가는 2026F PER 6배 중반\n" + "x" * 300)
        got = parse_header(text, "2026-07-01")
        self.assertEqual(got["target_price"], 78000)
        self.assertEqual(got["price_at_report"], 41400)
        self.assertEqual(got["price_at_report_date"], "2026-06-29")

    def test_year_with_suffix_is_not_price(self):
        got = parse_header("목표주가 78,000\n현재 주가는 2026F PER 6배 중반\n" + "x" * 300, "2026-07-01")
        self.assertIsNone(got["price_at_report"])

    def test_man_won_unit(self):
        """iM 고려아연: 본문 '목표주가 163만원' 이 먼저 나온다 → 163 이 아니라 1,630,000."""
        text = ("동사에 대한 투자의견 Buy와 목표주가 163만원 [기존 170만원]을 제시한다.\n"
                "목표주가(12M) 1,630,000원(하향)\n" + "x" * 300)
        self.assertEqual(parse_header(text, "2026-08-07")["target_price"], 1630000)

    def test_invalid_date_is_none_not_garbage(self):
        got = parse_header("목표주가 1,000원\n현재주가 (13/40) 800원\n" + "x" * 300, "2026-07-02")
        self.assertIsNone(got["price_at_report_date"])
        self.assertEqual(got["price_at_report"], 800)

    def test_missing_fields_are_none_not_zero(self):
        text = "본문만 있고 헤더 없음.\n" + "x" * 300
        got = parse_header(text, "2026-08-18")
        for key in ("target_price", "price_at_report", "price_at_report_date",
                    "upside_printed", "prev_target_printed", "opinion"):
            self.assertIsNone(got[key], key)


class ClassifyStatus(unittest.TestCase):
    def test_image_pdf(self):
        self.assertEqual(classify_status("짧은텍스트", {}), STATUS_IMAGE_PDF)

    def test_no_target(self):
        text = "x" * MIN_TEXT_CHARS
        self.assertEqual(classify_status(text, {"target_price": None}), STATUS_NO_TARGET)

    def test_ok(self):
        text = "x" * MIN_TEXT_CHARS
        self.assertEqual(classify_status(text, {"target_price": 6500}), STATUS_OK)


if __name__ == "__main__":
    unittest.main()
