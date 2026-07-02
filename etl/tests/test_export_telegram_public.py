"""export_telegram_public.py 단위 테스트.

검증 항목:
  1. fetch_posts: (channel, date_kst) 조회, post_id 오름차순, links_json 역직렬화
  2. to_json_str: 기존 임시 JSON과 유사한 키(id/post/posted_at_utc/posted_at_kst/date_kst/text/links)
  3. to_markdown: 사람이 읽을 수 있는 형태 (건수 헤더 + 항목별 시각/링크)
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.collect_telegram_public import ensure_schema, upsert_posts
from scripts.export_telegram_public import fetch_posts, to_json_str, to_markdown


class ExportTelegramPublicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "telegram_public.sqlite3"
        self.con = sqlite3.connect(str(self.db))
        ensure_schema(self.con)
        upsert_posts(self.con, "butler_works", [
            {
                "id": 2, "post": "butler_works/2", "posted_at_utc": "2026-07-01T01:00:00+00:00",
                "date_kst": "2026-07-01", "text": "두번째", "links": ["https://a.example"],
            },
            {
                "id": 1, "post": "butler_works/1", "posted_at_utc": "2026-07-01T00:00:00+00:00",
                "date_kst": "2026-07-01", "text": "첫번째", "links": [],
            },
            {
                "id": 3, "post": "butler_works/3", "posted_at_utc": "2026-06-30T23:00:00+00:00",
                "date_kst": "2026-06-30", "text": "다른날", "links": [],
            },
        ])
        self.con.commit()

    def tearDown(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_posts_filters_and_orders_by_post_id(self):
        posts = fetch_posts(self.con, "butler_works", "2026-07-01")
        self.assertEqual([p["id"] for p in posts], [1, 2])

    def test_fetch_posts_deserializes_links(self):
        posts = fetch_posts(self.con, "butler_works", "2026-07-01")
        self.assertEqual(posts[1]["links"], ["https://a.example"])

    def test_fetch_posts_other_channel_empty(self):
        self.assertEqual(fetch_posts(self.con, "other_channel", "2026-07-01"), [])

    def test_to_json_str_keys_match_legacy_shape(self):
        posts = fetch_posts(self.con, "butler_works", "2026-07-01")
        data = json.loads(to_json_str(posts))
        self.assertEqual(len(data), 2)
        for key in ("id", "post", "posted_at_utc", "posted_at_kst", "date_kst", "text", "links"):
            self.assertIn(key, data[0])
        self.assertTrue(data[0]["posted_at_kst"].startswith("2026-07-01T09:00:00"))

    def test_to_markdown_contains_count_and_text(self):
        posts = fetch_posts(self.con, "butler_works", "2026-07-01")
        md = to_markdown("butler_works", "2026-07-01", posts)
        self.assertIn("2", md)  # 건수
        self.assertIn("첫번째", md)
        self.assertIn("두번째", md)


if __name__ == "__main__":
    unittest.main()
