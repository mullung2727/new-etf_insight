"""download_telegram_report_attachments.py 단위 테스트.

검증 항목:
  1. extract_ticker: `[기업]...종목명(코드.KS/의견)` 텍스트에서 (종목명, 코드) 추출.
     [시장] 등 종목코드 없는 글은 None.
  2. sanitize_name: 공백 trim + Windows 금지문자 제거
  3. is_report_link: stockinfo7 report/url 패턴만 True (article/* 등은 False)
  4. dest_path: {out_dir}/{name}_{code}/{date}_{post_id}.pdf 조합
  5. download_attachment: 신규 다운로드 / 파일 존재시 스킵(멱등)
  6. run(): DB에서 대상 글 필터링(리포트링크+종목코드 있는 것만) 후 다운로드,
     시장 리포트(종목코드 없음)는 스킵 카운트로 집계
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.collect_telegram_public import ensure_schema, upsert_posts
from scripts.download_telegram_report_attachments import (
    dest_path,
    download_attachment,
    extract_ticker,
    is_report_link,
    run,
    sanitize_name,
)


class ExtractTickerTest(unittest.TestCase):
    def test_company_report_extracts_name_and_code(self):
        text = "[기업][한국전력] 한국전력(015760.KS/매수): SMP 상한제가 다시 언급될 만큼 쉽지 않은 상황"
        self.assertEqual(extract_ticker(text), ("한국전력", "015760"))

    def test_kosdaq_code_suffix(self):
        text = "[기업][에코프로비엠] 에코프로비엠(247540.KQ/중립): 코멘트"
        self.assertEqual(extract_ticker(text), ("에코프로비엠", "247540"))

    def test_market_report_returns_none(self):
        text = "[시장] [CrediVille] 크레딧 시장 단기부동화 심화"
        self.assertIsNone(extract_ticker(text))

    def test_no_ticker_pattern_returns_none(self):
        self.assertIsNone(extract_ticker("아무 텍스트"))


class SanitizeNameTest(unittest.TestCase):
    def test_strips_whitespace(self):
        self.assertEqual(sanitize_name(" 풍산 "), "풍산")

    def test_removes_windows_illegal_chars(self):
        self.assertEqual(sanitize_name('A/B:C*D?E"F<G>H|I'), "ABCDEFGHI")


class IsReportLinkTest(unittest.TestCase):
    def test_report_url_matches(self):
        self.assertTrue(is_report_link("https://stockinfo7.com/stock/report/url/126863"))

    def test_article_summary_link_does_not_match(self):
        self.assertFalse(is_report_link("https://stockinfo7.com/article/report/588"))

    def test_unrelated_link_does_not_match(self):
        self.assertFalse(is_report_link("https://example.com/foo"))


class DestPathTest(unittest.TestCase):
    def test_builds_expected_path(self):
        out_dir = Path("/tmp/out")
        p = dest_path(out_dir, "한국전력", "015760", "2026-07-01", 100764)
        self.assertEqual(p, out_dir / "한국전력_015760" / "2026-07-01_100764.pdf")


class DownloadAttachmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_downloads_when_missing(self):
        dest = self.tmp / "sub" / "file.pdf"
        calls = []

        def fake_fetch(url, timeout=40):
            calls.append(url)
            return b"%PDF-fake-bytes"

        result = download_attachment("https://x/y", dest, fetch_fn=fake_fetch)
        self.assertTrue(result)
        self.assertEqual(dest.read_bytes(), b"%PDF-fake-bytes")
        self.assertEqual(calls, ["https://x/y"])

    def test_skips_when_already_exists(self):
        dest = self.tmp / "file.pdf"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"existing")

        def fake_fetch(url, timeout=40):
            raise AssertionError("should not fetch when file already exists")

        result = download_attachment("https://x/y", dest, fetch_fn=fake_fetch)
        self.assertFalse(result)
        self.assertEqual(dest.read_bytes(), b"existing")


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "telegram_public.sqlite3"
        self.out_dir = self.tmp / "out"
        self.con = sqlite3.connect(str(self.db))
        ensure_schema(self.con)
        upsert_posts(self.con, "companyreport", [
            {
                "id": 100764, "post": "companyreport/100764",
                "posted_at_utc": "2026-04-14T00:00:00+00:00", "date_kst": "2026-04-14",
                "text": "[기업][한국전력] 한국전력(015760.KS/매수): SMP 상한제",
                "links": ["https://stockinfo7.com/stock/report/url/126863"],
            },
            {
                "id": 100762, "post": "companyreport/100762",
                "posted_at_utc": "2026-04-14T00:00:00+00:00", "date_kst": "2026-04-14",
                "text": "[시장] [CrediVille] 크레딧 시장 단기부동화 심화",
                "links": ["https://stockinfo7.com/stock/report/url/126862"],
            },
            {
                "id": 100768, "post": "companyreport/100768",
                "posted_at_utc": "2026-04-14T00:00:00+00:00", "date_kst": "2026-04-14",
                "text": "[리포트] 2026-04-14 종목(기업) 리포트 정리(오늘)",
                "links": ["https://stockinfo7.com/article/report/588"],
            },
        ])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_run_downloads_only_ticker_report_links(self):
        fetched_urls = []

        def fake_fetch(url, timeout=40):
            fetched_urls.append(url)
            return b"%PDF-x"

        stats = run(
            self.con, "companyreport", "2026-04-14", self.out_dir, fetch_fn=fake_fetch,
        )
        self.assertEqual(fetched_urls, ["https://stockinfo7.com/stock/report/url/126863"])
        self.assertEqual(stats["matched"], 1)
        self.assertEqual(stats["downloaded"], 1)
        self.assertEqual(stats["skipped_no_ticker"], 2)
        self.assertEqual(stats["skipped_exists"], 0)

        saved = self.out_dir / "한국전력_015760" / "2026-04-14_100764.pdf"
        self.assertTrue(saved.exists())

    def test_run_idempotent_second_call_skips_existing(self):
        def fake_fetch(url, timeout=40):
            return b"%PDF-x"

        run(self.con, "companyreport", "2026-04-14", self.out_dir, fetch_fn=fake_fetch)

        def fail_fetch(url, timeout=40):
            raise AssertionError("should not re-download")

        stats = run(self.con, "companyreport", "2026-04-14", self.out_dir, fetch_fn=fail_fetch)
        self.assertEqual(stats["downloaded"], 0)
        self.assertEqual(stats["skipped_exists"], 1)


if __name__ == "__main__":
    unittest.main()
