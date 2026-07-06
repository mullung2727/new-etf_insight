"""telegram_stock_insights.py (크로스채널 종목탐색/분석 테이블) 단위 테스트.

검증 항목:
  1. ensure_schema: 멱등
  2. upsert_candidate: INSERT/UPDATE, UNIQUE(date_kst, session, ticker)
  3. upsert_candidate 재실행(재탐색)은 analysis를 건드리지 않는다 (분석 단계 전용 필드 보존)
  4. update_analysis: analysis만 갱신, 탐색 필드는 그대로
  5. 같은 (date_kst, ticker)라도 session이 다르면 별도 행
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.telegram_stock_insights import (
    ensure_schema,
    update_analysis,
    upsert_candidate,
)


class StockInsightsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.con = sqlite3.connect(str(self.tmp / "t.sqlite3"))
        ensure_schema(self.con)

    def tearDown(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ensure_schema_idempotent(self):
        ensure_schema(self.con)  # 두 번째 호출도 에러 없음

    def test_upsert_candidate_insert_then_update(self):
        upsert_candidate(
            self.con, "2026-07-01", "close", "005930", "삼성전자",
            mention_channels=["getfeed"], source_post_refs=["getfeed/1"],
            discovery_reason="이름 언급 1건",
        )
        row = self.con.execute(
            "SELECT name, mention_channels, source_post_refs, discovery_reason, analysis "
            "FROM telegram_stock_insights WHERE date_kst=? AND session=? AND ticker=?",
            ("2026-07-01", "close", "005930"),
        ).fetchone()
        self.assertEqual(row[0], "삼성전자")
        self.assertIn("getfeed", row[1])
        self.assertIn("getfeed/1", row[2])
        self.assertIsNone(row[4])

        upsert_candidate(
            self.con, "2026-07-01", "close", "005930", "삼성전자",
            mention_channels=["getfeed", "corevalue"],
            source_post_refs=["getfeed/1", "corevalue/9"],
            discovery_reason="이름 언급 2건",
        )
        row = self.con.execute(
            "SELECT mention_channels, source_post_refs, discovery_reason FROM telegram_stock_insights "
            "WHERE date_kst=? AND session=? AND ticker=?",
            ("2026-07-01", "close", "005930"),
        ).fetchone()
        self.assertIn("corevalue", row[0])
        self.assertIn("corevalue/9", row[1])
        self.assertEqual(row[2], "이름 언급 2건")

    def test_upsert_candidate_does_not_clobber_existing_analysis(self):
        upsert_candidate(
            self.con, "2026-07-01", "close", "005930", "삼성전자",
            mention_channels=["getfeed"], source_post_refs=["getfeed/1"],
            discovery_reason="이름 언급 1건",
        )
        update_analysis(self.con, "2026-07-01", "close", "005930", "분석결과 텍스트")

        # 재탐색(재실행) — analysis는 그대로 유지되어야 함
        upsert_candidate(
            self.con, "2026-07-01", "close", "005930", "삼성전자",
            mention_channels=["getfeed", "kimcharger"],
            source_post_refs=["getfeed/1", "kimcharger/3"],
            discovery_reason="이름 언급 2건",
        )
        row = self.con.execute(
            "SELECT analysis, mention_channels FROM telegram_stock_insights "
            "WHERE date_kst=? AND session=? AND ticker=?",
            ("2026-07-01", "close", "005930"),
        ).fetchone()
        self.assertEqual(row[0], "분석결과 텍스트")
        self.assertIn("kimcharger", row[1])

    def test_update_analysis_only_touches_analysis(self):
        upsert_candidate(
            self.con, "2026-07-01", "close", "005930", "삼성전자",
            mention_channels=["getfeed"], source_post_refs=["getfeed/1"],
            discovery_reason="이름 언급 1건",
        )
        update_analysis(self.con, "2026-07-01", "close", "005930", "분석완료")
        row = self.con.execute(
            "SELECT discovery_reason, analysis FROM telegram_stock_insights "
            "WHERE date_kst=? AND session=? AND ticker=?",
            ("2026-07-01", "close", "005930"),
        ).fetchone()
        self.assertEqual(row[0], "이름 언급 1건")
        self.assertEqual(row[1], "분석완료")

    def test_same_ticker_different_session_are_separate_rows(self):
        for sess in ("morning", "close", "evening"):
            upsert_candidate(
                self.con, "2026-07-01", sess, "005930", "삼성전자",
                mention_channels=["getfeed"], source_post_refs=[f"getfeed/{sess}"],
                discovery_reason=f"{sess} 언급",
            )
        n = self.con.execute(
            "SELECT COUNT(*) FROM telegram_stock_insights WHERE date_kst=? AND ticker=?",
            ("2026-07-01", "005930"),
        ).fetchone()[0]
        self.assertEqual(n, 3)
        # update_analysis는 해당 세션 행만 건드린다
        update_analysis(self.con, "2026-07-01", "close", "005930", "close분석")
        rows = dict(self.con.execute(
            "SELECT session, analysis FROM telegram_stock_insights WHERE date_kst=? AND ticker=?",
            ("2026-07-01", "005930"),
        ).fetchall())
        self.assertEqual(rows["close"], "close분석")
        self.assertIsNone(rows["morning"])
        self.assertIsNone(rows["evening"])


if __name__ == "__main__":
    unittest.main()
