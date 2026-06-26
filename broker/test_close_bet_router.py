"""close_bet 라우터 단위 테스트.

broker는 pytest가 없으므로 stdlib unittest + FastAPI TestClient로 돌린다.
    .venv/Scripts/python.exe -m unittest test_close_bet_router

watchlist DB는 임시 파일을 CLOSE_BET_DB_PATH로 주입한다(라우터만 격리 테스트).
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import close_bet

_DDL = """
CREATE TABLE close_bet_orders (
    date TEXT, ticker TEXT, score INTEGER, qty INTEGER, order_type TEXT,
    status TEXT, order_no TEXT, message TEXT, raw TEXT, created_at TEXT,
    cntr_price INTEGER, cntr_qty INTEGER, verified_at TEXT,
    sell_order_no TEXT, sell_status TEXT, sell_price INTEGER, sell_qty INTEGER,
    sold_at TEXT, exit_reason TEXT, pnl_pct REAL,
    PRIMARY KEY (date, ticker)
)
"""


def _today() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")


class _Base(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Path(path)
        os.environ["CLOSE_BET_DB_PATH"] = str(self.db)
        self.today = _today()
        self._seed()
        app = FastAPI()
        app.include_router(close_bet.router)
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("CLOSE_BET_DB_PATH", None)
        self.db.unlink(missing_ok=True)

    def _seed(self):
        con = sqlite3.connect(str(self.db))
        con.execute(_DDL)
        rows = [
            # 오늘 매수, 미청산
            (self.today, "AAA", 80, 1, "market", "confirmed", "0001",
             1000, None, None, "2026-06-22 06:19:01"),
            # 어제 이전 매수 + 미청산 (watching)
            ("20260101", "BBB", 75, 1, "market", "confirmed", "0002",
             2000, None, None, "2026-01-01 06:19:00"),
            # 청산 완료 (history)
            ("20251231", "CCC", 90, 1, "market", "confirmed", "0003",
             3000, "filled", "tp", "2025-12-31 06:19:00"),
        ]
        for (date, tk, sc, qty, ot, st, ono, cp, sell_st, reason, created) in rows:
            con.execute(
                "INSERT INTO close_bet_orders "
                "(date,ticker,score,qty,order_type,status,order_no,cntr_price,"
                " sell_status,sell_price,sell_qty,exit_reason,pnl_pct,sold_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [date, tk, sc, qty, ot, st, ono, cp,
                 sell_st, (cp + 50 if sell_st else None),
                 (1 if sell_st else None), reason,
                 (5.0 if sell_st else None),
                 ("2026-06-21T15:19:00+09:00" if sell_st else None),
                 created],
            )
        con.commit()
        con.close()


class TestPositions(_Base):
    def _get(self) -> dict:
        resp = self.client.get("/close-bet/positions")
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_buys_all_dates_desc(self):
        # 매수현황 = 전체 매수(청산된 CCC 포함), date 내림차순
        data = self._get()
        self.assertEqual([r["ticker"] for r in data["buys"]], ["AAA", "BBB", "CCC"])
        self.assertEqual([r["ticker"] for r in data["watching"]], ["BBB"])
        self.assertEqual([r["ticker"] for r in data["history"]], ["CCC"])

    def test_buy_fields_with_created_at(self):
        row = self._get()["buys"][0]
        self.assertEqual(row["score"], 80)
        self.assertEqual(row["cntr_price"], 1000)
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["created_at"], "2026-06-22 06:19:01")

    def test_history_has_pnl_no_reason(self):
        row = self._get()["history"][0]
        self.assertNotIn("exit_reason", row)  # 사유 제거됨
        self.assertEqual(row["date"], "20251231")  # 매수일(watchlist 일자) 표시
        self.assertEqual(row["pnl_pct"], 5.0)
        self.assertEqual(row["sell_price"], 3050)

    def test_watching_excludes_today_and_sold(self):
        # 오늘 매수(AAA)·청산완료(CCC)는 watching에 없어야
        tickers = [r["ticker"] for r in self._get()["watching"]]
        self.assertNotIn("AAA", tickers)
        self.assertNotIn("CCC", tickers)


class TestEmpty(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        self.db = Path(path)
        os.environ["CLOSE_BET_DB_PATH"] = str(self.db)
        con = sqlite3.connect(str(self.db))
        con.execute(_DDL)
        con.commit()
        con.close()
        app = FastAPI()
        app.include_router(close_bet.router)
        self.client = TestClient(app)

    def tearDown(self):
        os.environ.pop("CLOSE_BET_DB_PATH", None)
        self.db.unlink(missing_ok=True)

    def test_empty_returns_empty_buckets(self):
        data = self.client.get("/close-bet/positions").json()
        self.assertEqual(data, {"buys": [], "watching": [], "history": []})


if __name__ == "__main__":
    unittest.main()
