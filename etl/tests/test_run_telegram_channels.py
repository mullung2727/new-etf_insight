"""run_telegram_channels.py (통합 러너) 단위 테스트.

검증 항목:
  1. process_channel: 채널 1개 텍스트 수집(crawl+upsert)
  2. run_all: 여러 채널 순회, 한 채널 실패해도 다른 채널 계속(에러 격리)
  3. run_all: only 필터

(증권사 리포트 PDF는 네이버 별도 배치 — test_download_naver_research.py 참조.)
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.collect_telegram_public import ensure_schema
from scripts.run_telegram_channels import process_channel, run_all


def _page(post_id, iso_utc, text):
    return (
        f'<div class="tgme_widget_message" data-post="companyreport/{post_id}">'
        f'<div class="tgme_widget_message_text">{text}</div>'
        f'<time datetime="{iso_utc}"></time>'
        f'</div>'
    )


class ProcessChannelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.con = sqlite3.connect(str(self.tmp / "t.sqlite3"))
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _collect_fetch(self, channel, before=None, timeout=40):
        # 첫 페이지에 글 1개, 이후 빈 페이지(=중단)
        if before is None:
            return _page(100764, "2026-04-14T00:00:00+00:00", "[기업][한국전력] 코멘트")
        return ""

    def test_collects_and_upserts(self):
        cfg = {"source_url": "https://t.me/s/companyreport"}
        result = process_channel(
            self.con, "companyreport", cfg, "2026-04-14",
            collect_fetch_fn=self._collect_fetch, sleep_fn=lambda s: None,
        )
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["inserted"], 1)
        self.assertNotIn("attachments", result)

    def test_collects_date_range_single_pass(self):
        # 04-13~04-15 한 번의 역방향 패스로 수집(날짜별 재크롤 없음)
        pages = {
            None: _page(3, "2026-04-15T00:00:00+00:00", "d15"),
            3: _page(2, "2026-04-14T00:00:00+00:00", "d14"),
            2: _page(1, "2026-04-13T00:00:00+00:00", "d13"),
            1: _page(0, "2026-04-12T00:00:00+00:00", "d12"),  # 하한 미만 -> 중단
        }
        calls = []

        def collect_fetch(channel, before=None, timeout=40):
            calls.append(before)
            return pages.get(before, "")

        result = process_channel(
            self.con, "companyreport", cfg={"source_url": "https://t.me/s/companyreport"},
            date_kst="2026-04-13", end_date="2026-04-15",
            collect_fetch_fn=collect_fetch, sleep_fn=lambda s: None,
        )
        self.assertEqual(result["fetched"], 3)  # d13,d14,d15
        self.assertEqual(calls, [None, 3, 2, 1])  # 단일 역방향 패스


class RunAllTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.con = sqlite3.connect(str(self.tmp / "t.sqlite3"))
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_one_channel_failure_does_not_block_others(self):
        channels = {
            "good": {"source_url": "https://t.me/s/good"},
            "bad": {"source_url": "https://t.me/s/bad"},
        }

        def collect_fetch(channel, before=None, timeout=40):
            if channel == "bad":
                raise RuntimeError("boom")
            if before is None:
                return _page(1, "2026-04-14T00:00:00+00:00", "그냥 글")
            return ""

        results, errors = run_all(
            self.con, channels, "2026-04-14",
            collect_fetch_fn=collect_fetch, sleep_fn=lambda s: None,
        )
        self.assertEqual([r["channel"] for r in results], ["good"])
        self.assertEqual([c for c, _ in errors], ["bad"])

    def test_only_filter_processes_single_channel(self):
        channels = {
            "good": {"source_url": "https://t.me/s/good"},
            "other": {"source_url": "https://t.me/s/other"},
        }

        def collect_fetch(channel, before=None, timeout=40):
            if before is None:
                return _page(1, "2026-04-14T00:00:00+00:00", "글")
            return ""

        results, errors = run_all(
            self.con, channels, "2026-04-14", only="good",
            collect_fetch_fn=collect_fetch, sleep_fn=lambda s: None,
        )
        self.assertEqual([r["channel"] for r in results], ["good"])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
