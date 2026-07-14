from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import duckdb

from research.watchlist_expected_return.phase1_data_audit import (
    audit_databases,
    render_markdown,
    write_results,
)


class Phase1DataAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.watchlist_db = root / "watchlist.sqlite3"
        self.krx_db = root / "krx.duckdb"
        self.output_dir = root / "results"

        with closing(sqlite3.connect(self.watchlist_db)) as con, con:
            con.execute("CREATE TABLE watchlist(date TEXT, stock_code TEXT, PRIMARY KEY(date, stock_code))")
            con.execute("CREATE TABLE llm_scores(date TEXT, ticker TEXT, score INTEGER, PRIMARY KEY(date,ticker))")
            con.execute("CREATE TABLE intraday_ranking(date TEXT, rank INTEGER, ticker TEXT)")
            con.execute("""CREATE TABLE close_bet_orders(
                date TEXT, ticker TEXT, qty INTEGER, cntr_price INTEGER, cntr_qty INTEGER,
                sell_price INTEGER, sell_qty INTEGER, pnl_pct REAL, sell_cmsn INTEGER, sell_tax INTEGER
            )""")
            con.executemany("INSERT INTO watchlist VALUES (?,?)", [
                ("20260102", "000001"), ("20260102", "000002"), ("20260105", "000001")
            ])
            con.executemany("INSERT INTO llm_scores VALUES (?,?,?)", [
                ("20260102", "000001", 80), ("20260102", "000002", 60)
            ])
            con.execute("INSERT INTO intraday_ranking VALUES ('20260102',1,'000001')")
            con.execute("INSERT INTO close_bet_orders VALUES ('20260102','000001',10,101,10,110,10,8.5,10,20)")

        with duckdb.connect(str(self.krx_db)) as con:
            con.execute("""CREATE TABLE ohlcv(
                date VARCHAR, ticker VARCHAR, market VARCHAR, open INTEGER, high INTEGER,
                low INTEGER, close INTEGER, volume BIGINT, trading_value BIGINT,
                market_cap BIGINT, list_shrs BIGINT
            )""")
            rows = []
            for date in ("20260102", "20260105", "20260106", "20260107", "20260108", "20260109"):
                rows.append((date, "000001", "KOSPI", 100, 111, 90, 100, 1000, 100000, 1000000, 10000))
            rows.append(("20260102", "000002", "KOSDAQ", 200, 210, 190, 200, 500, 100000, 500000, 2500))
            con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_audit_reports_join_and_forward_coverage(self) -> None:
        audit = audit_databases(self.watchlist_db, self.krx_db)
        coverage = audit["join_coverage"]
        self.assertEqual(coverage["watchlist_rows"], 3)
        self.assertEqual(coverage["score_rows_joined"], 2)
        self.assertEqual(coverage["entry_close_covered"], 3)
        self.assertEqual(coverage["forward_ohlcv"]["d_plus_1"]["eligible"], 3)
        self.assertEqual(coverage["forward_ohlcv"]["d_plus_1"]["covered"], 2)
        self.assertEqual(coverage["phase2_primary_cohort"], 1)
        self.assertEqual(audit["actual_orders"]["close_to_fill_slippage"]["count"], 1)
        self.assertFalse(audit["temporal_integrity"]["score_generated_at_available"])

    def test_result_files_are_reusable_json_and_markdown(self) -> None:
        audit = audit_databases(self.watchlist_db, self.krx_db)
        json_path, md_path = write_results(audit, self.output_dir)
        self.assertTrue(json_path.exists())
        self.assertIn("미래 OHLCV 연결", md_path.read_text(encoding="utf-8"))
        self.assertIn("watchlist 표본: 3건", render_markdown(audit))


if __name__ == "__main__":
    unittest.main()
