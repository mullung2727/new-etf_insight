"""run_watchlist_research.py 안전장치 단위 테스트.

검증 항목:
  1. upsert_one_score: 단건 즉시 upsert, 중복 시 replace
  2. scoring_deadline_time: 마감 시각 도달 시 루프 중단
  3. scoring_timeout_per_symbol: 종목당 타임아웃 초과 시 skip row 반환
"""
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.run_watchlist_research import (
    _is_past_deadline,
    _research_with_timeout,
    upsert_one_score,
)
from scripts.wl_sqlite import connect_ro

_SAMPLE_ROW = {
    "date": "2026-06-15",
    "ticker": "005930",
    "name": "삼성전자",
    "ratio": 2.5,
    "today_volume": 1000000,
    "avg5_volume": 400000,
    "trading_value": 50000000000,
    "close": 75000,
    "score": 85,
    "category": "개별재료",
    "reason_summary": "테스트 요약",
    "final_opinion": "테스트 의견",
    "evidence_board": "종토방: 테스트",
    "evidence_news": "뉴스: 테스트",
    "evidence_web": "웹: 테스트",
    "sources": ["https://example.com"],
    "raw": {},
}


class TestUpsertOneScore(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        os.unlink(path)  # 새 DB로 생성되도록 빈 경로 확보
        self.db_path = Path(path)

    def tearDown(self):
        self.db_path.unlink(missing_ok=True)

    def _row_count(self, date: str, ticker: str) -> int:
        with connect_ro(self.db_path) as con:
            return con.execute(
                "SELECT COUNT(*) FROM llm_scores WHERE date=? AND ticker=?",
                [date, ticker],
            ).fetchone()[0]

    def test_creates_table_and_inserts(self):
        upsert_one_score(self.db_path, _SAMPLE_ROW)
        self.assertEqual(self._row_count("20260615", "005930"), 1)

    def test_upsert_replaces_on_duplicate(self):
        upsert_one_score(self.db_path, _SAMPLE_ROW)
        updated = {**_SAMPLE_ROW, "score": 90}
        upsert_one_score(self.db_path, updated)
        with connect_ro(self.db_path) as con:
            score = con.execute(
                "SELECT score FROM llm_scores WHERE date='20260615' AND ticker='005930'"
            ).fetchone()[0]
        self.assertEqual(score, 90)
        self.assertEqual(self._row_count("20260615", "005930"), 1)

    def test_idempotent_multiple_calls(self):
        for _ in range(3):
            upsert_one_score(self.db_path, _SAMPLE_ROW)
        self.assertEqual(self._row_count("20260615", "005930"), 1)


class TestDeadlineGuard(unittest.TestCase):
    def _make_naive_time(self, h: int, m: int, s: int = 0) -> str:
        return f"{h:02d}:{m:02d}:{s:02d}"

    def test_no_deadline_returns_false(self):
        self.assertFalse(_is_past_deadline(None))

    def test_past_deadline_returns_true(self):
        # 00:00:00 은 항상 이미 지난 시각
        with patch("scripts.run_watchlist_research._now_seoul") as mock_now:
            mock_now.return_value = datetime(2026, 6, 15, 15, 18, 0)
            self.assertTrue(_is_past_deadline("15:17:00"))

    def test_before_deadline_returns_false(self):
        with patch("scripts.run_watchlist_research._now_seoul") as mock_now:
            mock_now.return_value = datetime(2026, 6, 15, 15, 10, 0)
            self.assertFalse(_is_past_deadline("15:17:00"))

    def test_exact_deadline_returns_true(self):
        with patch("scripts.run_watchlist_research._now_seoul") as mock_now:
            mock_now.return_value = datetime(2026, 6, 15, 15, 17, 0)
            self.assertTrue(_is_past_deadline("15:17:00"))


class TestPerSymbolTimeout(unittest.TestCase):
    def _fast_research(self, *args, **kwargs) -> dict:
        return {**_SAMPLE_ROW, "score": 85}

    def _slow_research(self, *args, **kwargs) -> dict:
        time.sleep(5)
        return {**_SAMPLE_ROW, "score": 85}

    def test_fast_research_returns_result(self):
        result = _research_with_timeout(
            self._fast_research, "20260615", "005930", {}, timeout_sec=2.0
        )
        self.assertEqual(result["score"], 85)
        self.assertNotEqual(result.get("category"), "scoring timeout")

    def test_slow_research_returns_skip_row(self):
        result = _research_with_timeout(
            self._slow_research, "20260615", "005930", {}, timeout_sec=0.2
        )
        self.assertEqual(result["ticker"], "005930")
        self.assertEqual(result["score"], 0)
        self.assertIn("timeout", result["category"])

    def test_skip_row_has_required_fields(self):
        result = _research_with_timeout(
            self._slow_research, "20260615", "005930", {}, timeout_sec=0.2
        )
        required = {"date", "ticker", "name", "score", "category", "reason_summary",
                    "final_opinion", "evidence_board", "evidence_news", "evidence_web", "sources"}
        self.assertTrue(required.issubset(result.keys()))


if __name__ == "__main__":
    unittest.main()
