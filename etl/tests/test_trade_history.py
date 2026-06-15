"""Stage 1: kiwoom_trade_history DDL + upsert 테스트."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import duckdb

# 아직 구현 전 — 이 import가 RED 상태
from scripts.run_close_bet import (
    create_kiwoom_trade_history_table,
    upsert_trade_history,
)

_DATE = "20260615"


def _fresh_db() -> Path:
    fd, path = tempfile.mkstemp(suffix=".duckdb")
    os.close(fd)
    os.unlink(path)
    return Path(path)


def _count(db: Path) -> int:
    with duckdb.connect(str(db), read_only=True) as con:
        return con.execute("SELECT COUNT(*) FROM kiwoom_trade_history").fetchone()[0]


def _fetch(db: Path, order_no: str) -> dict | None:
    with duckdb.connect(str(db), read_only=True) as con:
        row = con.execute(
            "SELECT order_no, ticker, status, source FROM kiwoom_trade_history WHERE order_no=?",
            [order_no],
        ).fetchone()
    return {"order_no": row[0], "ticker": row[1], "status": row[2], "source": row[3]} if row else None


def _sample_row(order_no: str = "0000001", ticker: str = "005930") -> dict:
    return {
        "order_no": order_no,
        "date": _DATE,
        "ticker": ticker,
        "side": "buy",
        "order_type": "market",
        "qty": 1,
        "price": 5000,
        "status": "submitted",
        "source": "close_bet",
        "raw": "{}",
    }


class TestCreateKiwoomTradeHistoryTable(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_creates_table(self):
        with duckdb.connect(str(self.db)) as con:
            create_kiwoom_trade_history_table(con)
            cols = {r[0] for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='kiwoom_trade_history'"
            ).fetchall()}
        self.assertIn("order_no", cols)
        self.assertIn("ticker", cols)
        self.assertIn("side", cols)
        self.assertIn("source", cols)
        self.assertIn("price", cols)
        self.assertIn("created_at", cols)

    def test_idempotent(self):
        with duckdb.connect(str(self.db)) as con:
            create_kiwoom_trade_history_table(con)
            create_kiwoom_trade_history_table(con)  # 두 번 호출해도 에러 없음

    def test_order_no_is_primary_key(self):
        with duckdb.connect(str(self.db)) as con:
            create_kiwoom_trade_history_table(con)
            row = _sample_row()
            con.execute(
                "INSERT INTO kiwoom_trade_history VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                [row["order_no"], row["date"], row["ticker"], row["side"], row["order_type"],
                 row["qty"], row["price"], row["status"], row["source"], row["raw"]],
            )
            # 동일 PK 삽입 시 에러
            with self.assertRaises(Exception):
                con.execute(
                    "INSERT INTO kiwoom_trade_history VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    [row["order_no"], row["date"], "000660", row["side"], row["order_type"],
                     row["qty"], row["price"], row["status"], row["source"], row["raw"]],
                )


class TestUpsertTradeHistory(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()
        with duckdb.connect(str(self.db)) as con:
            create_kiwoom_trade_history_table(con)

    def tearDown(self):
        self.db.unlink(missing_ok=True)

    def test_inserts_new_row(self):
        upsert_trade_history(self.db, _sample_row("0000001"))
        self.assertEqual(_count(self.db), 1)

    def test_skips_duplicate_order_no(self):
        row = _sample_row("0000001")
        upsert_trade_history(self.db, row)
        upsert_trade_history(self.db, {**row, "ticker": "000660"})  # 다른 ticker, 같은 order_no
        self.assertEqual(_count(self.db), 1)
        self.assertEqual(_fetch(self.db, "0000001")["ticker"], "005930")  # 원본 유지

    def test_different_order_nos_both_inserted(self):
        upsert_trade_history(self.db, _sample_row("0000001"))
        upsert_trade_history(self.db, _sample_row("0000002", "000660"))
        self.assertEqual(_count(self.db), 2)

    def test_dry_run_order_no_format(self):
        row = _sample_row()
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        row["order_no"] = f"DRY_005930_{ts}"
        row["status"] = "dry_run"
        upsert_trade_history(self.db, row)
        result = _fetch(self.db, row["order_no"])
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "dry_run")

    def test_stores_source_field(self):
        upsert_trade_history(self.db, _sample_row("0000001"))
        self.assertEqual(_fetch(self.db, "0000001")["source"], "close_bet")

    def test_creates_table_if_not_exists(self):
        # 테이블 없는 새 DB에서도 동작
        fresh = _fresh_db()
        try:
            upsert_trade_history(fresh, _sample_row("0000001"))
            self.assertEqual(_count(fresh), 1)
        finally:
            fresh.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
