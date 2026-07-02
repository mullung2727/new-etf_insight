"""collect_telegram_public.py 단위 테스트.

검증 항목:
  1. normalize_channel: URL(slug/`/s/` 포함)/직접 channel 입력 처리
  2. parse_messages: 실제 tgme_widget_message 구조 파싱 (data-post/time/text/links)
  3. clean_text: <br> 개행 변환 + 태그 제거 + HTML unescape
  4. crawl_date: KST 날짜 필터 + pagination 중단 조건 + 빈 첫 페이지 에러
  5. ensure_schema + upsert_posts: 테이블 생성, INSERT/UPDATE 멱등, UNIQUE(channel, post_id)
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.collect_telegram_public import (
    clean_text,
    crawl_date,
    ensure_schema,
    normalize_channel,
    parse_messages,
    upsert_channel,
    upsert_posts,
)


def _msg_html(post_id: int, iso_utc: str, text: str) -> str:
    return (
        f'<div class="tgme_widget_message text_not_supported_wrap js-widget_message" '
        f'data-post="butler_works/{post_id}" data-view="x">'
        f'<div class="tgme_widget_message_bubble">'
        f'<div class="tgme_widget_message_info short js-message_info">'
        f'<a href="https://t.me/butler_works/{post_id}"><time datetime="{iso_utc}">시간</time></a>'
        f'</div>'
        f'<div class="tgme_widget_message_text js-message_text">{text}</div>'
        f'</div></div>'
    )


class NormalizeChannelTest(unittest.TestCase):
    def test_channel_url_plain(self):
        self.assertEqual(normalize_channel("https://t.me/butler_works", None), "butler_works")

    def test_channel_url_with_s_prefix(self):
        self.assertEqual(normalize_channel("https://t.me/s/butler_works", None), "butler_works")

    def test_channel_url_trailing_slash(self):
        self.assertEqual(normalize_channel("https://t.me/butler_works/", None), "butler_works")

    def test_direct_channel_arg(self):
        self.assertEqual(normalize_channel(None, "@butler_works"), "butler_works")

    def test_requires_one_of(self):
        with self.assertRaises(ValueError):
            normalize_channel(None, None)


class CleanTextTest(unittest.TestCase):
    def test_br_to_newline(self):
        self.assertEqual(clean_text("a<br/>b<br>c"), "a\nb\nc")

    def test_strips_tags_and_unescapes(self):
        self.assertEqual(clean_text('<b>제목</b> &amp; 부제'), "제목 & 부제")


class ParseMessagesTest(unittest.TestCase):
    def test_parses_post_id_time_text(self):
        page = _msg_html(20660, "2026-07-01T22:10:00+00:00", "본문 텍스트")
        msgs = parse_messages(page)
        self.assertEqual(len(msgs), 1)
        m = msgs[0]
        self.assertEqual(m["id"], 20660)
        self.assertEqual(m["post"], "butler_works/20660")
        self.assertEqual(m["date_kst"], "2026-07-02")  # UTC 22:10 -> KST 07-02 07:10
        self.assertEqual(m["text"], "본문 텍스트")

    def test_extracts_links(self):
        text = '내용 <a href="https://example.com/a">링크</a> 계속 <a href="https://example.com/b">링크2</a>'
        page = _msg_html(1, "2026-07-01T00:00:00+00:00", text)
        msgs = parse_messages(page)
        self.assertEqual(msgs[0]["links"], ["https://example.com/a", "https://example.com/b"])

    def test_multiple_messages_on_page(self):
        page = (
            _msg_html(10, "2026-07-01T00:00:00+00:00", "글1")
            + _msg_html(11, "2026-07-01T01:00:00+00:00", "글2")
        )
        msgs = parse_messages(page)
        self.assertEqual([m["id"] for m in msgs], [10, 11])

    def test_no_messages_returns_empty(self):
        self.assertEqual(parse_messages("<html><body>no posts here</body></html>"), [])

    def test_media_only_message_keeps_empty_text(self):
        # tgme_widget_message_text div 자체가 없는 미디어 전용 글도 text=''로 유지
        page = (
            '<div class="tgme_widget_message" data-post="butler_works/99">'
            '<time datetime="2026-07-01T00:00:00+00:00"></time>'
            '</div>'
        )
        msgs = parse_messages(page)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "")

    def test_time_after_text_div_does_not_corrupt_next_message(self):
        # footer 구조(<time>이 text div 뒤)에서도 각 메시지 텍스트가 서로 섞이지 않아야 함
        page = (
            '<div class="tgme_widget_message" data-post="butler_works/1">'
            '<div class="tgme_widget_message_text">첫번째 본문</div>'
            '<time datetime="2026-07-01T00:00:00+00:00"></time>'
            '</div>'
            '<div class="tgme_widget_message" data-post="butler_works/2">'
            '<div class="tgme_widget_message_text">두번째 본문</div>'
            '<time datetime="2026-07-01T01:00:00+00:00"></time>'
            '</div>'
        )
        msgs = parse_messages(page)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["text"], "첫번째 본문")
        self.assertEqual(msgs[1]["text"], "두번째 본문")

    def test_parses_real_captured_page_fixture(self):
        # 2026-07-02 t.me/s/butler_works 실제 응답에서 발췌한 원본 마크업(2개 글).
        fixture = Path(__file__).parent / "fixtures" / "telegram_public_sample.html"
        page = fixture.read_text(encoding="utf-8")
        msgs = parse_messages(page)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["post"], "butler_works/20660")
        self.assertTrue(msgs[0]["text"])  # 실제 본문이 비어있지 않게 파싱됨
        self.assertEqual(msgs[0]["posted_at_utc"], "2026-07-02T01:36:06+00:00")
        self.assertEqual(msgs[0]["date_kst"], "2026-07-02")  # UTC 01:36 -> KST 10:36 same day


class CrawlDateTest(unittest.TestCase):
    def test_filters_by_kst_date_and_stops_pagination(self):
        # 3 pages, newest→oldest by `before`. target date 2026-07-01(KST).
        page1 = (
            _msg_html(30, "2026-07-01T10:00:00+00:00", "d1")  # KST 07-01 19:00
            + _msg_html(29, "2026-07-01T09:00:00+00:00", "d2")  # KST 07-01 18:00
        )
        page2 = (
            _msg_html(28, "2026-06-30T14:00:00+00:00", "d3")  # KST 06-30 23:00
            + _msg_html(27, "2026-06-30T13:00:00+00:00", "d4")  # KST 06-30 22:00
        )
        pages = {None: page1, 29: page2}
        calls = []

        def fake_fetch(channel, before=None, timeout=40):
            calls.append(before)
            return pages.get(before, "")

        msgs = crawl_date("butler_works", "2026-07-01", max_pages=10, fetch_fn=fake_fetch, sleep_fn=lambda s: None)
        self.assertEqual([m["id"] for m in msgs], [29, 30])
        # page2 전부 target보다 과거이므로 3페이지째 호출 없이 중단
        self.assertEqual(calls, [None, 29])

    def test_empty_first_page_raises(self):
        def fake_fetch(channel, before=None, timeout=40):
            return "<html>no posts</html>"

        with self.assertRaises(RuntimeError):
            crawl_date("dead_channel", "2026-07-01", fetch_fn=fake_fetch, sleep_fn=lambda s: None)

    def test_respects_max_pages(self):
        # 매 페이지 target보다 최신 글만 있어 중단 조건이 걸리지 않는 경우 max_pages로 멈춰야 함
        call_count = {"n": 0}

        def fake_fetch(channel, before=None, timeout=40):
            call_count["n"] += 1
            pid = 100 - call_count["n"]
            return _msg_html(pid, "2026-07-01T05:00:00+00:00", "always today")

        crawl_date("butler_works", "2026-07-01", max_pages=3, fetch_fn=fake_fetch, sleep_fn=lambda s: None)
        self.assertEqual(call_count["n"], 3)


class SqliteUpsertTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "telegram_public.sqlite3"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _connect(self):
        con = sqlite3.connect(str(self.db))
        ensure_schema(con)
        return con

    def test_ensure_schema_idempotent(self):
        con = self._connect()
        ensure_schema(con)  # 두 번 호출해도 에러 없음
        con.close()

    def test_upsert_posts_insert_then_update(self):
        con = self._connect()
        msgs = [{
            "id": 1, "post": "butler_works/1", "posted_at_utc": "2026-07-01T00:00:00+00:00",
            "date_kst": "2026-07-01", "text": "원문", "links": [],
        }]
        inserted, updated = upsert_posts(con, "butler_works", msgs)
        self.assertEqual((inserted, updated), (1, 0))

        msgs[0]["text"] = "수정된 원문"
        inserted, updated = upsert_posts(con, "butler_works", msgs)
        self.assertEqual((inserted, updated), (0, 1))

        row = con.execute(
            "SELECT text FROM telegram_posts WHERE channel=? AND post_id=?",
            ("butler_works", 1),
        ).fetchone()
        self.assertEqual(row[0], "수정된 원문")
        con.close()

    def test_upsert_posts_no_duplicate_rows(self):
        con = self._connect()
        msgs = [{
            "id": 5, "post": "butler_works/5", "posted_at_utc": "2026-07-01T00:00:00+00:00",
            "date_kst": "2026-07-01", "text": "x", "links": [],
        }]
        upsert_posts(con, "butler_works", msgs)
        upsert_posts(con, "butler_works", msgs)
        cnt = con.execute("SELECT COUNT(*) FROM telegram_posts").fetchone()[0]
        self.assertEqual(cnt, 1)
        con.close()

    def test_upsert_channel(self):
        con = self._connect()
        upsert_channel(con, "butler_works", "https://t.me/s/butler_works")
        row = con.execute(
            "SELECT source_url FROM telegram_channels WHERE channel=?", ("butler_works",)
        ).fetchone()
        self.assertEqual(row[0], "https://t.me/s/butler_works")
        con.close()


if __name__ == "__main__":
    unittest.main()
