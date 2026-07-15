"""run_close_bet.py 단위 테스트.

검증 항목:
  1. close_bet_orders 테이블 DDL (verify 컬럼 포함, 멱등)
  2. check_precondition: llm_scores 행 수 반환
  3. load_order_candidates: 임계값 필터 / score DESC 정렬 / max_order_count 제한 / 이미 주문된 종목 제외
  4. upsert_order_result: 삽입 / 중복 (date, ticker) PK 방어
  5. _is_in_order_window: 시간창 체크 / allow_outside 우회
"""
import os
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import sqlite3

import duckdb

from scripts.run_close_bet import (
    _is_in_order_window,
    budget_for,
    check_precondition,
    confirm_fills,
    create_close_bet_orders_table,
    fetch_market_caps,
    load_close_bet_status,
    load_order_candidates,
    normalize_date_key,
    parse_args,
    qty_from_budget,
    rank_and_cut,
    upsert_order_result,
)
from scripts.wl_sqlite import connect_ro, connect_rw
from scripts.run_pullback_order import create_pullback_orders_table

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


class TestDateKey(unittest.TestCase):
    def test_keeps_compact_date(self):
        self.assertEqual(normalize_date_key("20260629"), "20260629")

    def test_converts_dashed_date(self):
        self.assertEqual(normalize_date_key("2026-06-29"), "20260629")

    def test_rejects_invalid_date_key(self):
        with self.assertRaises(ValueError):
            normalize_date_key("2026/06/29")


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
        result = load_order_candidates(self.db, _DATE, score_threshold=80)
        self.assertEqual(result, [])

    def test_filters_below_threshold(self):
        self._seed([
            {"date": _DATE, "ticker": "005930", "score": 85},
            {"date": _DATE, "ticker": "000660", "score": 75},
        ])
        result = load_order_candidates(self.db, _DATE, score_threshold=80)
        tickers = [r["ticker"] for r in result]
        self.assertIn("005930", tickers)
        self.assertNotIn("000660", tickers)

    def test_sorted_by_score_desc(self):
        self._seed([
            {"date": _DATE, "ticker": "A", "score": 82},
            {"date": _DATE, "ticker": "B", "score": 95},
            {"date": _DATE, "ticker": "C", "score": 88},
        ])
        result = load_order_candidates(self.db, _DATE, score_threshold=80)
        scores = [r["score"] for r in result]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_returns_all_above_threshold(self):
        # cut은 rank_and_cut 책임 — load는 threshold 통과분 전체 반환
        self._seed([
            {"date": _DATE, "ticker": str(i), "score": 80 + i} for i in range(10)
        ])
        result = load_order_candidates(self.db, _DATE, score_threshold=80)
        self.assertEqual(len(result), 10)

    def test_excludes_already_ordered(self):
        self._seed(
            [
                {"date": _DATE, "ticker": "005930", "score": 90},
                {"date": _DATE, "ticker": "000660", "score": 85},
            ],
            already_ordered=["005930"],
        )
        result = load_order_candidates(self.db, _DATE, score_threshold=80)
        tickers = [r["ticker"] for r in result]
        self.assertNotIn("005930", tickers)
        self.assertIn("000660", tickers)

    def test_excludes_open_pullback_position(self):
        self._seed([{"date": _DATE, "ticker": "005930", "score": 90}])
        with connect_rw(self.db) as con:
            create_pullback_orders_table(con)
            con.execute(
                "INSERT INTO pullback_orders (watchlist_date, signal_date, ticker, strategy, "
                "prior_low, day_open, signal_price, qty, status, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("20260613", _DATE, "005930", "lower_low_bullish_reversal",
                 100, 99, 101, 1, "confirmed", "2026-06-15"),
            )
        self.assertEqual(load_order_candidates(self.db, _DATE, 80), [])


class TestCloseBetStatus(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        self.log_dir = Path(tempfile.mkdtemp())
        with connect_rw(self.db) as con:
            _seed_llm_scores(con, [
                {"date": "20260629", "ticker": "122350", "name": "삼기", "score": 80},
                {"date": "20260629", "ticker": "012340", "name": "뉴인텍", "score": 76},
                {"date": "20260629", "ticker": "086520", "name": "에코프로", "score": 76},
                {"date": "20260629", "ticker": "037710", "name": "광주신세계", "score": 54},
            ])
            create_close_bet_orders_table(con)

    def tearDown(self):
        self.db.unlink(missing_ok=True)
        for path in self.log_dir.glob("*"):
            path.unlink()
        self.log_dir.rmdir()

    def test_status_uses_compact_date_key_and_threshold_rows(self):
        status = load_close_bet_status(self.db, "2026-06-29", log_dir=self.log_dir)

        self.assertEqual(status["date_key"], "20260629")
        self.assertEqual(status["score_count"], 4)
        self.assertEqual(status["threshold_count"], 3)
        self.assertEqual([r["ticker"] for r in status["threshold_rows"]], ["122350", "012340", "086520"])
        self.assertEqual(status["order_count"], 0)
        self.assertEqual(status["final_judgment"], "failed before order")


class TestScheduledTaskContract(unittest.TestCase):
    def test_close_bet_order_wrapper_args_match_python_cli(self):
        repo_root = Path(__file__).resolve().parents[2]
        wrapper = repo_root / "ops" / "scheduled-tasks" / "run-close-bet-order.ps1"
        text = wrapper.read_text(encoding="utf-8")

        self.assertNotIn("--max-order-count", text)
        self.assertNotIn("--qty-per-symbol", text)

        match = re.search(r"\$pythonArgs\s*=\s*@\((.*?)\)", text, re.S)
        self.assertIsNotNone(match)
        args = re.findall(r'"([^"]*)"', match.group(1))
        self.assertEqual(args[0], "scripts\\run_close_bet.py")

        # score_threshold 는 close_bet.json(config)이 소스 → ps1 인자에서 제거됨.
        self.assertNotIn("--score-threshold", text)
        parsed = parse_args(args[1:])
        self.assertIsNone(parsed.score_threshold)  # 미지정 → main 에서 config 값
        self.assertEqual(parsed.dry_run, "false")
        self.assertEqual(parsed.broker_url, "http://localhost:8001")


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


# ── 7. 예산 배분 순수 함수 (G1) ───────────────────────────────────────────────

class TestBudgetFor(unittest.TestCase):
    def test_one_stock_3m(self):
        self.assertEqual(budget_for(1), 3_000_000)

    def test_two_stocks_2m_each(self):
        self.assertEqual(budget_for(2), 2_000_000)

    def test_three_stocks_split_5m(self):
        # 500만 ÷ 3, floor
        self.assertEqual(budget_for(3), 1_666_666)

    def test_zero_or_out_of_range(self):
        # 정의역 밖(0, 4+)은 0 — 호출측이 N∈{1,2,3}로 보장하지만 방어
        self.assertEqual(budget_for(0), 0)
        self.assertEqual(budget_for(4), 0)


class TestQtyFromBudget(unittest.TestCase):
    def test_floor_division(self):
        # 200만 / 12,340 = 162.07 → 162
        self.assertEqual(qty_from_budget(2_000_000, 12_340), 162)

    def test_price_exceeds_budget_zero(self):
        self.assertEqual(qty_from_budget(1_666_666, 2_000_000), 0)

    def test_zero_price_zero(self):
        self.assertEqual(qty_from_budget(2_000_000, 0), 0)


class TestRankAndCut(unittest.TestCase):
    def _c(self, ticker, score):
        return {"ticker": ticker, "score": score}

    def test_cuts_to_three(self):
        cands = [self._c(str(i), 90 - i) for i in range(5)]
        result = rank_and_cut(cands, {}, n=3)
        self.assertEqual(len(result), 3)

    def test_score_desc_primary(self):
        cands = [self._c("A", 80), self._c("B", 95), self._c("C", 88)]
        result = rank_and_cut(cands, {}, n=3)
        self.assertEqual([r["ticker"] for r in result], ["B", "C", "A"])

    def test_market_cap_breaks_score_tie(self):
        # score 동점 4개 → 시총 큰 3개만, 시총 DESC 정렬
        cands = [self._c("A", 80), self._c("B", 80), self._c("C", 80), self._c("D", 80)]
        caps = {"A": 100, "B": 400, "C": 300, "D": 200}
        result = rank_and_cut(cands, caps, n=3)
        self.assertEqual([r["ticker"] for r in result], ["B", "C", "D"])

    def test_missing_cap_treated_as_zero(self):
        # 시총 없는 종목은 0 취급 → 동점 시 맨 뒤
        cands = [self._c("A", 80), self._c("B", 80)]
        caps = {"A": 500}  # B 없음
        result = rank_and_cut(cands, caps, n=3)
        self.assertEqual([r["ticker"] for r in result], ["A", "B"])


# ── 8. fetch_market_caps (DuckDB, G2) ────────────────────────────────────────

class TestFetchMarketCaps(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".duckdb")
        os.close(fd)
        os.unlink(path)  # duckdb가 새로 생성
        self.krx = Path(path)
        con = duckdb.connect(str(self.krx))
        con.execute("""
            CREATE TABLE ohlcv (
                date VARCHAR, ticker VARCHAR, market_cap BIGINT,
                PRIMARY KEY (date, ticker)
            )
        """)
        con.executemany(
            "INSERT INTO ohlcv (date, ticker, market_cap) VALUES (?,?,?)",
            [
                ("20260612", "005930", 100),
                ("20260615", "005930", 500),   # 대상일 — 최신
                ("20260616", "005930", 999),    # 대상일 이후 — 무시
                ("20260613", "000660", 300),    # 대상일 이전 최신
            ],
        )
        con.close()

    def tearDown(self):
        self.krx.unlink(missing_ok=True)

    def test_returns_latest_on_or_before_date(self):
        caps = fetch_market_caps(self.krx, ["005930", "000660"], "20260615")
        self.assertEqual(caps["005930"], 500)   # 20260616(999) 무시
        self.assertEqual(caps["000660"], 300)   # 20260613 최신

    def test_missing_ticker_absent(self):
        caps = fetch_market_caps(self.krx, ["005930", "999999"], "20260615")
        self.assertNotIn("999999", caps)

    def test_empty_tickers(self):
        self.assertEqual(fetch_market_caps(self.krx, [], "20260615"), {})


if __name__ == "__main__":
    unittest.main()
