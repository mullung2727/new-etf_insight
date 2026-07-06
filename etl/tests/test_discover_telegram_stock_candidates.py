"""discover_telegram_stock_candidates.py 단위 테스트.

검증 항목:
  1. extract_mentions: 종목명 substring / 6자리 코드 언급 매칭, 같은 종목 중복 제거
  2. aggregate_candidates: 여러 글/채널 걸쳐 종목별로 채널·post_ref 취합
  3. fetch_discovery_posts: telegram_posts에서 지정 채널+날짜만 조회
"""
import sqlite3
import unittest

from scripts.discover_telegram_stock_candidates import (
    aggregate_candidates,
    extract_mentions,
    fetch_discovery_posts,
)

NAME_TO_CODE = {"삼성전자": "005930", "카카오": "035720"}
CODE_TO_NAME = {v: k for k, v in NAME_TO_CODE.items()}


class ExtractMentionsTest(unittest.TestCase):
    def test_matches_name_substring(self):
        got = extract_mentions("오늘 삼성전자 52주 신고가", NAME_TO_CODE, CODE_TO_NAME)
        self.assertEqual(got, [("005930", "삼성전자")])

    def test_matches_code_regex(self):
        got = extract_mentions("코드 035720 거래량 급증", NAME_TO_CODE, CODE_TO_NAME)
        self.assertEqual(got, [("035720", "카카오")])

    def test_dedupes_name_and_code_of_same_stock(self):
        got = extract_mentions("삼성전자(005930) 목표주가 상향", NAME_TO_CODE, CODE_TO_NAME)
        self.assertEqual(got, [("005930", "삼성전자")])

    def test_unknown_code_ignored(self):
        got = extract_mentions("코드 999999 알수없음", NAME_TO_CODE, CODE_TO_NAME)
        self.assertEqual(got, [])

    def test_no_mentions_returns_empty(self):
        got = extract_mentions("오늘 코스피 상승 마감", NAME_TO_CODE, CODE_TO_NAME)
        self.assertEqual(got, [])

    def test_multiple_distinct_stocks(self):
        got = extract_mentions("삼성전자와 카카오 동반 상승", NAME_TO_CODE, CODE_TO_NAME)
        self.assertEqual(set(got), {("005930", "삼성전자"), ("035720", "카카오")})


class AggregateCandidatesTest(unittest.TestCase):
    def test_groups_across_channels_and_posts(self):
        posts = [
            {"channel": "getfeed", "post_ref": "getfeed/1", "text": "삼성전자 신고가"},
            {"channel": "corevalue", "post_ref": "corevalue/9", "text": "삼성전자 목표가 상향"},
            {"channel": "getfeed", "post_ref": "getfeed/2", "text": "카카오 거래량 급증"},
        ]
        got = aggregate_candidates(posts, NAME_TO_CODE, CODE_TO_NAME)

        self.assertEqual(set(got), {"005930", "035720"})
        samsung = got["005930"]
        self.assertEqual(samsung["name"], "삼성전자")
        self.assertEqual(set(samsung["mention_channels"]), {"getfeed", "corevalue"})
        self.assertEqual(set(samsung["source_post_refs"]), {"getfeed/1", "corevalue/9"})
        self.assertIn("2", samsung["discovery_reason"])  # 2건 언급

        kakao = got["035720"]
        self.assertEqual(kakao["mention_channels"], ["getfeed"])
        self.assertEqual(kakao["source_post_refs"], ["getfeed/2"])

    def test_no_posts_returns_empty(self):
        self.assertEqual(aggregate_candidates([], NAME_TO_CODE, CODE_TO_NAME), {})


class FetchDiscoveryPostsTest(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.execute(
            "CREATE TABLE telegram_posts (channel TEXT, post_id INTEGER, post_ref TEXT, date_kst TEXT, text TEXT)"
        )
        rows = [
            ("getfeed", 1, "getfeed/1", "2026-07-01", "삼성전자 신고가"),
            ("getfeed", 2, "getfeed/2", "2026-07-02", "다른날"),  # 날짜 다름 -> 제외
            ("corevalue", 9, "corevalue/9", "2026-07-01", "카카오 언급"),
            ("butler_works", 5, "butler_works/5", "2026-07-01", "채널 대상 아님 -> 제외"),
        ]
        self.con.executemany(
            "INSERT INTO telegram_posts VALUES (?,?,?,?,?)", rows
        )

    def test_filters_by_channel_and_date(self):
        posts = fetch_discovery_posts(self.con, ["getfeed", "corevalue"], "2026-07-01")
        refs = {p["post_ref"] for p in posts}
        self.assertEqual(refs, {"getfeed/1", "corevalue/9"})


if __name__ == "__main__":
    unittest.main()
