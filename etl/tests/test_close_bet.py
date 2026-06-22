"""run_close_bet.py 단위 테스트.

검증 항목:
  1. close_bet_orders 테이블 DDL (verify 컬럼 포함, 멱등)
  2. check_precondition: llm_scores 행 수 반환
  3. load_order_candidates: 임계값 필터 / score DESC 정렬 / max_order_count 제한 / 이미 주문된 종목 제외
  4. upsert_order_result: 삽입 / 중복 (date, ticker) PK 방어
  5. _is_in_order_window: 시간창 체크 / allow_outside 우회
"""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import sqlite3

from scripts.run_close_bet import (
    _is_in_order_window,
    check_precondition,
    confirm_fills,
    create_close_bet_orders_table,
    load_order_candidates,
    upsert_order_result,
)
from scripts.wl_sqlite import connect_ro, connect_rw

# ── 공통 픽스처 ───────────────────────────────────────────────────────────────

_DATE = "20260615"

def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def _seed_llm_scores(con: sqlite3.Connection, rows: list[dict]) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS llm_scores (
            date VARCHAR, ticker VARCHAR, name VARCHAR,
            ratio DOUBLE, today_volume BIGINT, avg5_volume BIGINT,
            trading_value BIGINT, close INTEGER,
            score INTEGER, category VARCHAR, reason_summary TEXT,
            final_opinion TEXT, evidence_board TEXT, evidence_news TEXT,
            evidence_web TEXT, sources TEXT,
            PRIMARY KEY (date, ticker)
        )
    """)
    for r in rows:
        con.execute(
            "INSERT OR REPLACE INTO llm_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [r["date"], r["ticker"], r.get("name", r["ticker"]),
             None, None, None, None, r.get("close", 1000),
             r["score"], "테스트", "요약", "의견",
             "종토방", "뉴스", "웹", "[]"],
        )


def _seed_close_bet_orders(con: sqlite3.Connection, rows: list[dict]) -> None:
    create_close_bet_orders_table(con)
    for r in rows:
        con.execute(
            "INSERT OR REPLACE INTO close_bet_orders "
            "(date, ticker, score, qty, order_type, status, order_no, message, raw, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
            [r["date"], r["ticker"], r.get("score", 80), 1,
             "market", r.get("status", "submitted"), r.get("order_no", "0000001"), "", "{}"],
        )


# ── 1. DDL ────────────────────────────────────────────────────────────────────

class TestCreateTable(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_creates_table(self):
        with connect_rw(self.db) as con:
            create_close_bet_orders_table(con)
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info('close_bet_orders')"
            ).fetchall()}
        self.assertIn("order_no", cols)
        self.assertIn("status", cols)

    def test_has_verify_columns(self):
        with connect_rw(self.db) as con:
            create_close_bet_orders_table(con)
            cols = {r[1] for r in con.execute(
                "PRAGMA table_info('close_bet_orders')"
            ).fetchall()}
        self.assertIn("cntr_price", cols)
        self.assertIn("cntr_qty", cols)
        self.assertIn("verified_at", cols)

    def test_idempotent(self):
        with connect_rw(self.db) as con:
            create_close_bet_orders_table(con)
            create_close_bet_orders_table(con)  # 두 번 호출해도 에러 없음


# ── 2. precondition ───────────────────────────────────────────────────────────

class TestCheckPrecondition(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_returns_zero_when_no_scores(self):
        with connect_rw(self.db) as con:
            _seed_llm_scores(con, [])
        self.assertEqual(check_precondition(self.db, _DATE), 0)

    def test_returns_count_when_scores_exist(self):
        with connect_rw(self.db) as con:
            _seed_llm_scores(con, [
                {"date": _DATE, "ticker": "005930", "score": 85},
                {"date": _DATE, "ticker": "000660", "score": 72},
            ])
        self.assertEqual(check_precondition(self.db, _DATE), 2)

    def test_counts_only_target_date(self):
        with connect_rw(self.db) as con:
            _seed_llm_scores(con, [
                {"date": _DATE, "ticker": "005930", "score": 85},
                {"date": "20260614", "ticker": "000660", "score": 90},
            ])
        self.assertEqual(check_precondition(self.db, _DATE), 1)


# ── 3. load_order_candidates ─────────────────────────────────────────────────

class TestLoadOrderCandidates(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _seed(self, scores: list[dict], already_ordered: list[str] | None = None):
        with connect_rw(self.db) as con:
            _seed_llm_scores(con, scores)
            create_close_bet_orders_table(con)
            if already_ordered:
                _seed_close_bet_orders(con, [
                    {"date": _DATE, "ticker": t} for t in already_ordered
                ])

    def test_empty_when_no_scores(self):
        self._seed([])
        result = load_order_candidates(self.db, _DATE, score_threshold=80, max_order_count=5)
        self.assertEqual(result, [])

    def test_filters_below_threshold(self):
        self._seed([
            {"date": _DATE, "ticker": "005930", "score": 85},
            {"date": _DATE, "ticker": "000660", "score": 75},
        ])
        result = load_order_candidates(self.db, _DATE, score_threshold=80, max_order_count=5)
        tickers = [r["ticker"] for r in result]
        self.assertIn("005930", tickers)
        self.assertNotIn("000660", tickers)

    def test_sorted_by_score_desc(self):
        self._seed([
            {"date": _DATE, "ticker": "A", "score": 82},
            {"date": _DATE, "ticker": "B", "score": 95},
            {"date": _DATE, "ticker": "C", "score": 88},
        ])
        result = load_order_candidates(self.db, _DATE, score_threshold=80, max_order_count=5)
        scores = [r["score"] for r in result]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_max_order_count_cap(self):
        self._seed([
            {"date": _DATE, "ticker": str(i), "score": 80 + i} for i in range(10)
        ])
        result = load_order_candidates(self.db, _DATE, score_threshold=80, max_order_count=5)
        self.assertEqual(len(result), 5)

    def test_excludes_already_ordered(self):
        self._seed(
            [
                {"date": _DATE, "ticker": "005930", "score": 90},
                {"date": _DATE, "ticker": "000660", "score": 85},
            ],
            already_ordered=["005930"],
        )
        result = load_order_candidates(self.db, _DATE, score_threshold=80, max_order_count=5)
        tickers = [r["ticker"] for r in result]
        self.assertNotIn("005930", tickers)
        self.assertIn("000660", tickers)


# ── 4. upsert_order_result ────────────────────────────────────────────────────

class TestUpsertOrderResult(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        with connect_rw(self.db) as con:
            create_close_bet_orders_table(con)

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _count(self) -> int:
        with connect_ro(self.db) as con:
            return con.execute("SELECT COUNT(*) FROM close_bet_orders").fetchone()[0]

    def _fetch(self, ticker: str) -> dict | None:
        with connect_ro(self.db) as con:
            row = con.execute(
                "SELECT status, order_no FROM close_bet_orders WHERE date=? AND ticker=?",
                [_DATE, ticker],
            ).fetchone()
        return {"status": row[0], "order_no": row[1]} if row else None

    def test_inserts_new_row(self):
        upsert_order_result(self.db, {
            "date": _DATE, "ticker": "005930", "score": 85, "qty": 1,
            "order_type": "market", "status": "submitted",
            "order_no": "0000001", "message": "", "raw": "{}",
        })
        self.assertEqual(self._count(), 1)

    def test_skips_duplicate_date_ticker(self):
        row = {"date": _DATE, "ticker": "005930", "score": 85, "qty": 1,
               "order_type": "market", "status": "submitted",
               "order_no": "0000001", "message": "", "raw": "{}"}
        upsert_order_result(self.db, row)
        upsert_order_result(self.db, {**row, "order_no": "0000002"})
        self.assertEqual(self._count(), 1)
        self.assertEqual(self._fetch("005930")["order_no"], "0000001")  # 원본 유지


# ── 5. 시간창 가드 ─────────────────────────────────────────────────────────────

class TestOrderTimeWindow(unittest.TestCase):
    def _now(self, h: int, m: int, s: int = 0) -> datetime:
        return datetime(2026, 6, 15, h, m, s)

    def test_within_window_allowed(self):
        with patch("scripts.run_close_bet._now_seoul", return_value=self._now(15, 19, 30)):
            self.assertTrue(_is_in_order_window("15:19:00", "15:20:00", allow_outside=False))

    def test_before_window_blocked(self):
        with patch("scripts.run_close_bet._now_seoul", return_value=self._now(15, 18, 59)):
            self.assertFalse(_is_in_order_window("15:19:00", "15:20:00", allow_outside=False))

    def test_at_deadline_blocked(self):
        with patch("scripts.run_close_bet._now_seoul", return_value=self._now(15, 20, 0)):
            self.assertFalse(_is_in_order_window("15:19:00", "15:20:00", allow_outside=False))

    def test_after_deadline_blocked(self):
        with patch("scripts.run_close_bet._now_seoul", return_value=self._now(15, 20, 5)):
            self.assertFalse(_is_in_order_window("15:19:00", "15:20:00", allow_outside=False))

    def test_allow_outside_bypasses_window(self):
        with patch("scripts.run_close_bet._now_seoul", return_value=self._now(10, 0, 0)):
            self.assertTrue(_is_in_order_window("15:19:00", "15:20:00", allow_outside=True))


# ── 6. confirm_fills (매수 직후 kt00007 폴링 체결확정) ─────────────────────────

class TestConfirmFills(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        with connect_rw(self.db) as con:
            _seed_close_bet_orders(con, [
                {"date": _DATE, "ticker": "005930", "order_no": "0000050",
                 "status": "submitted"},
            ])

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def _row(self, ticker: str):
        with connect_ro(self.db) as con:
            return con.execute(
                "SELECT status, cntr_price, cntr_qty FROM close_bet_orders "
                "WHERE date=? AND ticker=?", [_DATE, ticker],
            ).fetchone()

    def test_confirms_on_match(self):
        # order_no "50"(정규화) ↔ 시드 "0000050" 매칭
        history = [{"order_no": "50", "cntr_qty": 1, "cntr_uv": 12340}]
        with patch("scripts.run_close_bet.fetch_order_history", return_value=history):
            remaining = confirm_fills(
                self.db, "http://x", _DATE, {"005930": "0000050"},
                sleep=lambda s: None,
            )
        self.assertEqual(remaining, {})
        status, cntr_price, cntr_qty = self._row("005930")
        self.assertEqual(status, "confirmed")
        self.assertEqual(cntr_price, 12340)
        self.assertEqual(cntr_qty, 1)

    def test_aggregates_partial_fills(self):
        # 동일 order_no 부분체결 → qty 합산, 단가는 첫 유효값
        history = [
            {"order_no": "50", "cntr_qty": 1, "cntr_uv": 12340},
            {"order_no": "50", "cntr_qty": 2, "cntr_uv": 12350},
        ]
        with patch("scripts.run_close_bet.fetch_order_history", return_value=history):
            confirm_fills(self.db, "x", _DATE, {"005930": "0000050"},
                          sleep=lambda s: None)
        _, cntr_price, cntr_qty = self._row("005930")
        self.assertEqual(cntr_qty, 3)
        self.assertEqual(cntr_price, 12340)

    def test_unmatched_stays_pending(self):
        # 체결내역 비면 미확정 → submitted 유지(16:00 배치 백업)
        with patch("scripts.run_close_bet.fetch_order_history", return_value=[]):
            remaining = confirm_fills(
                self.db, "x", _DATE, {"005930": "0000050"},
                max_attempts=2, interval=0, sleep=lambda s: None,
            )
        self.assertEqual(remaining, {"005930": "0000050"})
        self.assertEqual(self._row("005930")[0], "submitted")

    def test_retries_until_filled(self):
        # 첫 폴링 빈 응답 → 둘째 폴링에 체결 등장 → 확정
        responses = [[], [{"order_no": "50", "cntr_qty": 1, "cntr_uv": 999}]]
        with patch("scripts.run_close_bet.fetch_order_history",
                   side_effect=lambda url, date: responses.pop(0)):
            remaining = confirm_fills(
                self.db, "x", _DATE, {"005930": "0000050"},
                max_attempts=3, sleep=lambda s: None,
            )
        self.assertEqual(remaining, {})
        self.assertEqual(self._row("005930")[0], "confirmed")


if __name__ == "__main__":
    unittest.main()
