from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import duckdb

from research.watchlist_expected_return.phase2_score_relationship import (
    analyze_rows,
    load_analysis_rows,
    write_results,
)


class Phase2ScoreRelationshipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.watchlist_db = root / "watchlist.sqlite3"
        self.krx_db = root / "krx.duckdb"
        self.output_dir = root / "results"
        with closing(sqlite3.connect(self.watchlist_db)) as con, con:
            con.execute("CREATE TABLE watchlist(date TEXT, stock_code TEXT)")
            con.execute("""CREATE TABLE llm_scores(
                date TEXT, ticker TEXT, score INTEGER, category TEXT,
                ratio REAL, trading_value INTEGER
            )""")
            con.executemany("INSERT INTO watchlist VALUES (?,?)", [
                ("20260102", "000001"), ("20260102", "000002"), ("20260102", "000003")
            ])
            con.executemany("INSERT INTO llm_scores VALUES (?,?,?,?,?,?)", [
                ("20260102", "000001", 20, "원인불명", 2.0, 1000),
                ("20260102", "000002", 60, "복합요인", 5.0, 2000),
                ("20260102", "000003", 90, "개별재료", 10.0, 3000),
            ])
        with duckdb.connect(str(self.krx_db)) as con:
            con.execute("""CREATE TABLE ohlcv(
                date VARCHAR, ticker VARCHAR, market VARCHAR, open INTEGER, high INTEGER,
                low INTEGER, close INTEGER, volume BIGINT, trading_value BIGINT,
                market_cap BIGINT, list_shrs BIGINT
            )""")
            rows = []
            for ticker, next_open in (("000001", 90), ("000002", 100), ("000003", 110)):
                rows.append(("20260102", ticker, "KOSPI", 100, 100, 100, 100, 1, 1, 1, 1))
                rows.append(("20260105", ticker, "KOSPI", next_open, 115, 85, next_open, 1, 1, 1, 1))
            con.executemany("INSERT INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_load_and_analyze_positive_score_relationship(self) -> None:
        rows = load_analysis_rows(self.watchlist_db, self.krx_db)
        result = analyze_rows(rows)
        self.assertEqual(result["sample_count"], 3)
        self.assertGreater(result["correlations"]["gap_return_d1_open"]["pearson"], 0.99)
        self.assertEqual(result["score_bands_d1_open"]["80-100"]["positive_rate"], 1.0)
        self.assertEqual(result["score_bands_d1_open"]["40-59"]["count"], 0)

    def test_writes_reusable_result_and_row_files(self) -> None:
        rows = load_analysis_rows(self.watchlist_db, self.krx_db)
        result = analyze_rows(rows)
        paths = write_results(result, rows, self.output_dir)
        self.assertTrue(all(path.exists() for path in paths))
        self.assertIn("점수 구간별", paths[1].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
