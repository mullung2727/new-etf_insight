"""ensure_exit_columns 마이그레이션 단위 테스트.

검증:
  1. 빈 테이블 → 청산 7컬럼 추가
  2. 기존행 보존 + 신규컬럼 NULL
  3. 두 번 호출 멱등(에러 없음, 컬럼 중복 없음)
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.run_close_bet import (
    _EXIT_COLUMNS,
    create_close_bet_orders_table,
    ensure_exit_columns,
)
from scripts.wl_sqlite import connect_rw

_EXIT_NAMES = {c for c, _ in _EXIT_COLUMNS}


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def _columns(con: sqlite3.Connection) -> set[str]:
    return {r[1] for r in con.execute("PRAGMA table_info(close_bet_orders)")}


class TestEnsureExitColumns(unittest.TestCase):
    def setUp(self) -> None:
        self.db = _fresh_db()

    def tearDown(self) -> None:
        self.db.unlink(missing_ok=True)

    def test_adds_all_exit_columns(self):
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            cols = _columns(con)
        self.assertTrue(_EXIT_NAMES.issubset(cols), f"누락: {_EXIT_NAMES - cols}")

    def test_existing_row_preserved_with_null_exit_cols(self):
        with connect_rw(self.db) as con:
            create_close_bet_orders_table(con)
            con.execute(
                "INSERT INTO close_bet_orders (date, ticker, status, order_no) "
                "VALUES (?,?,?,?)",
                ["20260615", "005930", "filled", "0000050"],
            )
            ensure_exit_columns(con)
            row = con.execute(
                "SELECT status, sell_status, sell_price, exit_reason, pnl_pct "
                "FROM close_bet_orders WHERE ticker='005930'"
            ).fetchone()
        self.assertEqual(row[0], "filled")          # 기존값 보존
        self.assertIsNone(row[1])                    # sell_status NULL
        self.assertIsNone(row[2])                    # sell_price NULL
        self.assertIsNone(row[3])                    # exit_reason NULL
        self.assertIsNone(row[4])                    # pnl_pct NULL

    def test_idempotent(self):
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            ensure_exit_columns(con)  # 재호출 — ALTER 중복 에러 없어야
            cols = _columns(con)
        self.assertTrue(_EXIT_NAMES.issubset(cols))


if __name__ == "__main__":
    unittest.main()
