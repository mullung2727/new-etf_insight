"""run_close_bet main() 통합 테스트.

실제 API 호출 없이 main() 전체 흐름을 검증:
  1. precondition 실패 → sys.exit(1)
  2. 시간창 밖 + allow_outside=False → sys.exit(1)
  3. 시간창 밖 + allow_outside=True → 주문 진행
  4. dry_run=True → place_market_order 내부에서 HTTP 없이 'dry_run' 반환
  5. dry_run=False → place_market_order가 dry_run=False로 호출됨
  6. max_order_count 제한 → 상위 N개만 주문
  7. 마감 시각 루프 중 초과 → 일부만 처리하고 중단
"""
import contextlib
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import duckdb

from scripts.run_close_bet import (
    create_close_bet_orders_table,
)

_DATE = "20260615"
_DEFAULT_NOW = datetime(2026, 6, 15, 15, 19, 30)


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def _seed(db: Path, scores: list[tuple[str, int]]) -> None:
    with duckdb.connect(str(db)) as con:
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
        for ticker, score in scores:
            con.execute(
                "INSERT OR REPLACE INTO llm_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [_DATE, ticker, ticker, None, None, None, None, 1000,
                 score, "테스트", "요약", "의견", "종토방", "뉴스", "웹", "[]"],
            )
        create_close_bet_orders_table(con)


def _order_rows(db: Path) -> list[tuple]:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute(
            "SELECT ticker, status, order_no FROM close_bet_orders WHERE date=?",
            [_DATE],
        ).fetchall()


def _run_main(
    argv: list[str],
    db: Path,
    now_dt: datetime | None = None,
    cur_prc: int = 5000,
    order_result: dict | None = None,
    mock_place_order: bool = True,
) -> int:
    """main() 실행 헬퍼. sys.exit 코드 반환 (정상 종료 = 0).

    mock_place_order=False 시 실제 place_market_order를 호출 (dry_run 내부 로직 검증용).
    """
    mock_now = now_dt or _DEFAULT_NOW
    default_result = {"order_no": "0000099", "status": "submitted", "message": ""}

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("scripts.run_close_bet.DEFAULT_WATCHLIST_DB", db))
        stack.enter_context(patch("scripts.run_close_bet._now_seoul", return_value=mock_now))
        stack.enter_context(patch("scripts.run_close_bet.get_token", return_value="FAKE_TOKEN"))
        stack.enter_context(patch("scripts.run_close_bet.fetch_current_price", return_value=cur_prc))
        stack.enter_context(patch("scripts.run_close_bet.load_dotenv"))
        stack.enter_context(patch("time.sleep"))
        stack.enter_context(patch("sys.argv", ["run_close_bet.py"] + argv))
        if mock_place_order:
            stack.enter_context(patch(
                "scripts.run_close_bet.place_market_order",
                return_value=order_result or default_result,
            ))
        try:
            from scripts import run_close_bet
            run_close_bet.main()
            return 0
        except SystemExit as e:
            return int(e.code) if e.code is not None else 0


# ── 1. precondition ───────────────────────────────────────────────────────────

class TestPreconditionAbort(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_aborts_when_no_scores(self):
        _seed(self.db, [])
        code = _run_main(["--date", _DATE, "--allow-order-outside-close-window"], self.db)
        self.assertEqual(code, 1)
        self.assertEqual(_order_rows(self.db), [])

    def test_proceeds_when_scores_exist(self):
        _seed(self.db, [("005930", 85)])
        code = _run_main(["--date", _DATE, "--allow-order-outside-close-window"], self.db)
        self.assertEqual(code, 0)


# ── 2 & 3. 시간창 가드 ─────────────────────────────────────────────────────────

class TestTimeWindowGuard(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        _seed(self.db, [("005930", 85), ("000660", 82)])

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_blocks_before_window(self):
        code = _run_main(
            ["--date", _DATE, "--order-time", "15:19:00", "--order-deadline-time", "15:20:00"],
            self.db,
            now_dt=datetime(2026, 6, 15, 15, 18, 0),
        )
        self.assertEqual(code, 1)
        self.assertEqual(_order_rows(self.db), [])

    def test_blocks_at_or_after_deadline(self):
        code = _run_main(
            ["--date", _DATE, "--order-time", "15:19:00", "--order-deadline-time", "15:20:00"],
            self.db,
            now_dt=datetime(2026, 6, 15, 15, 20, 0),
        )
        self.assertEqual(code, 1)

    def test_allows_within_window(self):
        code = _run_main(
            ["--date", _DATE, "--order-time", "15:19:00", "--order-deadline-time", "15:20:00"],
            self.db,
            now_dt=datetime(2026, 6, 15, 15, 19, 30),
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(_order_rows(self.db)), 2)

    def test_allow_outside_bypasses_block(self):
        code = _run_main(
            ["--date", _DATE, "--allow-order-outside-close-window"],
            self.db,
            now_dt=datetime(2026, 6, 15, 10, 0, 0),
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(_order_rows(self.db)), 2)

    def test_allow_outside_before_market_open(self):
        code = _run_main(
            ["--date", _DATE, "--allow-order-outside-close-window"],
            self.db,
            now_dt=datetime(2026, 6, 15, 9, 0, 0),
        )
        self.assertEqual(code, 0)


# ── 4. dry_run=True ───────────────────────────────────────────────────────────

class TestDryRun(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        _seed(self.db, [("005930", 85), ("000660", 82)])

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_dry_run_true_records_dry_run_status(self):
        # mock_place_order=False → 실제 place_market_order 호출 (dry_run 내부 로직 동작)
        code = _run_main(
            ["--date", _DATE, "--dry-run", "true", "--allow-order-outside-close-window"],
            self.db,
            mock_place_order=False,
        )
        self.assertEqual(code, 0)
        rows = _order_rows(self.db)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r[1] == "dry_run" for r in rows))

    def test_dry_run_true_order_no_is_dry_run_marker(self):
        _run_main(
            ["--date", _DATE, "--dry-run", "true", "--allow-order-outside-close-window"],
            self.db,
            mock_place_order=False,
        )
        rows = _order_rows(self.db)
        self.assertTrue(all(r[2] == "DRY_RUN" for r in rows))

    def test_dry_run_false_calls_place_order_with_false(self):
        dry_run_args_seen: list[bool] = []

        def fake_place(token, host, ticker, qty, dry_run):
            dry_run_args_seen.append(dry_run)
            return {"order_no": "0000001", "status": "submitted", "message": ""}

        with patch("scripts.run_close_bet.DEFAULT_WATCHLIST_DB", self.db), \
             patch("scripts.run_close_bet._now_seoul", return_value=_DEFAULT_NOW), \
             patch("scripts.run_close_bet.get_token", return_value="FAKE"), \
             patch("scripts.run_close_bet.fetch_current_price", return_value=5000), \
             patch("scripts.run_close_bet.place_market_order", side_effect=fake_place), \
             patch("scripts.run_close_bet.load_dotenv"), \
             patch("time.sleep"), \
             patch("sys.argv", ["run_close_bet.py", "--date", _DATE, "--dry-run", "false",
                                "--allow-order-outside-close-window"]):
            from scripts import run_close_bet
            run_close_bet.main()

        self.assertEqual(len(dry_run_args_seen), 2)
        self.assertTrue(all(arg is False for arg in dry_run_args_seen))

        rows = _order_rows(self.db)
        self.assertTrue(all(r[1] == "submitted" for r in rows))


# ── 5. max_order_count ────────────────────────────────────────────────────────

class TestMaxOrderCount(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        # 8개 종목 seed (score 80~87)
        _seed(self.db, [(str(i), 80 + i) for i in range(8)])

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_caps_at_max_order_count(self):
        _run_main(
            ["--date", _DATE, "--max-order-count", "3", "--allow-order-outside-close-window"],
            self.db,
        )
        self.assertEqual(len(_order_rows(self.db)), 3)

    def test_top_scores_selected(self):
        _run_main(
            ["--date", _DATE, "--max-order-count", "3", "--allow-order-outside-close-window"],
            self.db,
        )
        tickers = {r[0] for r in _order_rows(self.db)}
        # score 87, 86, 85 → ticker "7", "6", "5"
        self.assertIn("7", tickers)
        self.assertIn("6", tickers)
        self.assertIn("5", tickers)
        self.assertNotIn("4", tickers)


# ── 6. 마감 시각 루프 중 초과 ─────────────────────────────────────────────────

class TestDeadlineMidLoop(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        _seed(self.db, [("A", 90), ("B", 85), ("C", 82)])

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_stops_when_deadline_reached_mid_loop(self):
        """루프 도중 15:20 초과 시 나머지 종목 중단."""
        call_count = 0

        def advancing_now():
            nonlocal call_count
            call_count += 1
            # 초기 시간창 확인 + 첫 번째 종목(A) 처리 = 2회까지 15:19:30
            # 두 번째 종목(B) 루프 시작부터 15:20:01 → break
            if call_count <= 2:
                return datetime(2026, 6, 15, 15, 19, 30)
            return datetime(2026, 6, 15, 15, 20, 1)

        with patch("scripts.run_close_bet.DEFAULT_WATCHLIST_DB", self.db), \
             patch("scripts.run_close_bet._now_seoul", side_effect=advancing_now), \
             patch("scripts.run_close_bet.get_token", return_value="FAKE"), \
             patch("scripts.run_close_bet.fetch_current_price", return_value=5000), \
             patch("scripts.run_close_bet.place_market_order",
                   return_value={"order_no": "0000001", "status": "submitted", "message": ""}), \
             patch("scripts.run_close_bet.load_dotenv"), \
             patch("time.sleep"), \
             patch("sys.argv", ["run_close_bet.py", "--date", _DATE,
                                "--order-time", "15:19:00", "--order-deadline-time", "15:20:00",
                                "--dry-run", "false"]):
            from scripts import run_close_bet
            run_close_bet.main()

        rows = _order_rows(self.db)
        # 3건 모두 처리되지 않고 일부만 기록
        self.assertLess(len(rows), 3)
        self.assertGreater(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
