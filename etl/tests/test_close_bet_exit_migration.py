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


_OLD_DDL = """
    CREATE TABLE close_bet_orders (
        date TEXT, ticker TEXT, score INTEGER, qty INTEGER, order_type TEXT, status TEXT,
        order_no TEXT, message TEXT, raw TEXT, created_at TEXT, cntr_price INTEGER,
        cntr_qty INTEGER, verified_at TEXT, PRIMARY KEY (date, ticker)
    )
"""


def _pk(con: sqlite3.Connection, table: str) -> list[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in sorted(rows, key=lambda r: r[5]) if r[5]]


class TestLegMigration(unittest.TestCase):
    """반반 분할 청산 — close_bet_orders PK (date,ticker,leg) + close_bet_sell_fills (T13)."""

    def setUp(self) -> None:
        self.db = _fresh_db()

    def tearDown(self) -> None:
        self.db.unlink(missing_ok=True)

    def test_fresh_db_has_leg_pk_and_fills_table(self):
        with connect_rw(self.db) as con:
            ensure_exit_columns(con)
            self.assertEqual(_pk(con, "close_bet_orders"), ["date", "ticker", "leg"])
            self.assertEqual(_pk(con, "close_bet_sell_fills"), ["date", "ticker", "leg", "order_no"])
            con.execute("INSERT INTO close_bet_orders (date, ticker) VALUES ('20260916','005160')")
            leg = con.execute("SELECT leg FROM close_bet_orders").fetchone()[0]
        self.assertEqual(leg, "single")

    def test_old_pk_rebuilt_with_rows_preserved(self):
        with connect_rw(self.db) as con:
            con.execute(_OLD_DDL)
            con.execute("ALTER TABLE close_bet_orders ADD COLUMN sell_status TEXT")
            con.execute(
                "INSERT INTO close_bet_orders (date, ticker, qty, status, cntr_price, cntr_qty, sell_status) "
                "VALUES ('20260915','052220',320,'confirmed',1870,320,'filled')")
            ensure_exit_columns(con)
            self.assertEqual(_pk(con, "close_bet_orders"), ["date", "ticker", "leg"])
            self.assertTrue(_EXIT_NAMES.issubset(_columns(con)))
            row = con.execute(
                "SELECT qty, status, cntr_price, cntr_qty, sell_status, leg FROM close_bet_orders").fetchone()
            self.assertEqual(row, (320, "confirmed", 1870, 320, "filled", "single"))
            # 같은 종목 다른 leg 는 허용, 같은 leg 중복은 거부
            con.execute("INSERT INTO close_bet_orders (date, ticker, leg) VALUES ('20260915','052220','chase')")
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("INSERT INTO close_bet_orders (date, ticker, leg) VALUES ('20260915','052220','chase')")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("close_bet_orders_old", tables)

    def test_rebuild_idempotent(self):
        with connect_rw(self.db) as con:
            con.execute(_OLD_DDL)
            con.execute("INSERT INTO close_bet_orders (date, ticker) VALUES ('20260915','052220')")
            ensure_exit_columns(con)
            ensure_exit_columns(con)
            count = con.execute("SELECT COUNT(*) FROM close_bet_orders").fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
