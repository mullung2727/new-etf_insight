"""run_verify 체결 대조 테스트.

broker GET /orders/history mock 기반:
  1. order_no 매칭 성공 → status='confirmed' + cntr_price/cntr_qty/verified_at
  2. 매칭 실패 → status='unconfirmed'
  3. order_no 0-padding 정규화 매칭 ("50" ↔ "0000050")
  4. broker 조회 실패(None) → exit(1), DB 변경 없음
  5. submitted/unconfirmed만 대조 (confirmed/dry_run/skipped 무시)
"""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.run_close_bet import create_close_bet_orders_table
from scripts.run_verify import normalize_order_no
from scripts.wl_sqlite import connect_ro, connect_rw

# Verification tests send status summaries in production. Keep fixture runs
# isolated from any real Discord webhook inherited from the shell.
os.environ.pop("DISCORD_WEBHOOK_URL", None)

_DATE = "20260615"
_NOW = datetime(2026, 6, 15, 16, 0, 0)


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def _seed_order(db: Path, ticker: str, status: str, order_no: str) -> None:
    with connect_rw(db) as con:
        create_close_bet_orders_table(con)
        con.execute(
            """INSERT INTO close_bet_orders
               (date, ticker, score, qty, order_type, status, order_no, message, raw, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            [_DATE, ticker, 85, 1, "market", status, order_no, "", "{}"],
        )


def _row(db: Path, ticker: str) -> dict:
    with connect_ro(db) as con:
        r = con.execute(
            "SELECT status, cntr_price, cntr_qty, verified_at FROM close_bet_orders "
            "WHERE date=? AND ticker=?",
            [_DATE, ticker],
        ).fetchone()
    return {"status": r[0], "cntr_price": r[1], "cntr_qty": r[2], "verified_at": r[3]}


def _run_main(db: Path, history: list[dict] | None) -> int:
    with patch("scripts.run_verify.DEFAULT_WATCHLIST_DB", db), \
         patch("scripts.run_verify._now_seoul", return_value=_NOW), \
         patch("scripts.run_verify.fetch_order_history", return_value=history), \
         patch("scripts.run_verify.load_dotenv"), \
         patch("sys.argv", ["run_verify.py", "--date", _DATE,
                            "--broker-url", "http://localhost:8001"]):
        from scripts import run_verify
        try:
            run_verify.main()
            return 0
        except SystemExit as e:
            return int(e.code) if e.code is not None else 0


class TestNormalizeOrderNo(unittest.TestCase):
    def test_strips_leading_zeros(self):
        self.assertEqual(normalize_order_no("0000050"), "50")
        self.assertEqual(normalize_order_no("50"), "50")
        self.assertEqual(normalize_order_no(50), "50")

    def test_all_zeros_or_empty(self):
        self.assertEqual(normalize_order_no("0000000"), "")
        self.assertEqual(normalize_order_no(""), "")
        self.assertEqual(normalize_order_no(None), "")


class TestVerifyMain(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_matched_marks_confirmed(self):
        _seed_order(self.db, "069500", "submitted", "0000050")
        history = [{"order_no": "0000050", "ticker": "069500",
                    "cntr_qty": 1, "cntr_uv": 12345, "ord_remnq": 0}]
        code = _run_main(self.db, history)
        self.assertEqual(code, 0)
        row = _row(self.db, "069500")
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["cntr_price"], 12345)
        self.assertEqual(row["cntr_qty"], 1)
        self.assertIsNotNone(row["verified_at"])

    def test_unmatched_marks_unconfirmed(self):
        _seed_order(self.db, "005930", "submitted", "0000099")
        history = [{"order_no": "0000050", "ticker": "069500",
                    "cntr_qty": 1, "cntr_uv": 12345, "ord_remnq": 0}]
        code = _run_main(self.db, history)
        self.assertEqual(code, 0)
        self.assertEqual(_row(self.db, "005930")["status"], "unconfirmed")

    def test_order_no_zero_padding_normalized(self):
        # close_bet에 "50"으로 저장, 키움 응답은 "0000050"
        _seed_order(self.db, "069500", "submitted", "50")
        history = [{"order_no": "0000050", "ticker": "069500",
                    "cntr_qty": 1, "cntr_uv": 555, "ord_remnq": 0}]
        code = _run_main(self.db, history)
        self.assertEqual(_row(self.db, "069500")["status"], "confirmed")
        self.assertEqual(_row(self.db, "069500")["cntr_price"], 555)

    def test_broker_failure_aborts_without_change(self):
        _seed_order(self.db, "069500", "submitted", "0000050")
        code = _run_main(self.db, None)  # fetch_order_history → None
        self.assertEqual(code, 1)
        self.assertEqual(_row(self.db, "069500")["status"], "submitted")  # 그대로

    def test_unconfirmed_rechecked_and_confirmed(self):
        _seed_order(self.db, "069500", "unconfirmed", "0000050")
        history = [{"order_no": "0000050", "ticker": "069500",
                    "cntr_qty": 1, "cntr_uv": 700, "ord_remnq": 0}]
        _run_main(self.db, history)
        self.assertEqual(_row(self.db, "069500")["status"], "confirmed")

    def test_confirmed_and_dry_run_untouched(self):
        _seed_order(self.db, "AAA", "confirmed", "0000010")
        _seed_order(self.db, "BBB", "dry_run", "DRY_BBB_x")
        _seed_order(self.db, "CCC", "skipped", "")
        history = [{"order_no": "0000010", "ticker": "AAA",
                    "cntr_qty": 1, "cntr_uv": 100, "ord_remnq": 0}]
        _run_main(self.db, history)
        # confirmed는 재대조 대상 아님 → cntr_price 안 채워짐(원래 NULL 유지)
        self.assertEqual(_row(self.db, "AAA")["status"], "confirmed")
        self.assertIsNone(_row(self.db, "AAA")["cntr_price"])
        self.assertEqual(_row(self.db, "BBB")["status"], "dry_run")
        self.assertEqual(_row(self.db, "CCC")["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
