"""query_telegram_public.py — 수집된 텔레그램 원문 조회 쿼리빌더 테스트.

검증 항목:
  1. build_query: 키워드/채널/기간 필터 조합에 따른 SQL+params 생성
  2. 실제 조회: in-memory DB에 넣고 키워드 검색이 맞는 행만 반환
"""
import sqlite3
import unittest

from scripts.query_telegram_public import build_query, run_query


class BuildQueryTest(unittest.TestCase):
    def test_keyword_only(self):
        sql, params = build_query(keyword="삼성전자")
        self.assertIn("text LIKE ?", sql)
        self.assertIn("%삼성전자%", params)

    def test_channel_and_date_range(self):
        sql, params = build_query(keyword=None, channel="getfeed", start="2026-07-01", end="2026-07-06")
        self.assertIn("channel = ?", sql)
        self.assertIn("date_kst BETWEEN ? AND ?", sql)
        self.assertEqual(params, ["getfeed", "2026-07-01", "2026-07-06"])

    def test_no_filters_still_valid(self):
        sql, params = build_query()
        self.assertIn("SELECT", sql)
        self.assertEqual(params, [])
        self.assertNotIn("WHERE", sql)


class RunQueryTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(
            "CREATE TABLE telegram_posts (channel TEXT, post_id INTEGER, post_ref TEXT, "
            "posted_at_utc TEXT, date_kst TEXT, text TEXT, links_json TEXT)"
        )
        rows = [
            ("getfeed", 1, "getfeed/1", "2026-07-01T00:00:00+00:00", "2026-07-01", "삼성전자 신고가", "[]"),
            ("corevalue", 2, "corevalue/2", "2026-07-02T00:00:00+00:00", "2026-07-02", "카카오 급등", "[]"),
            ("getfeed", 3, "getfeed/3", "2026-07-03T00:00:00+00:00", "2026-07-03", "삼성전자 목표가 상향", "[]"),
        ]
        self.con.executemany("INSERT INTO telegram_posts VALUES (?,?,?,?,?,?,?)", rows)

    def tearDown(self):
        self.con.close()

    def test_keyword_matches_only_relevant(self):
        got = run_query(self.con, keyword="삼성전자")
        self.assertEqual({r["post_ref"] for r in got}, {"getfeed/1", "getfeed/3"})

    def test_keyword_plus_channel_and_range(self):
        got = run_query(self.con, keyword="삼성전자", channel="getfeed", start="2026-07-03", end="2026-07-03")
        self.assertEqual([r["post_ref"] for r in got], ["getfeed/3"])


if __name__ == "__main__":
    unittest.main()
