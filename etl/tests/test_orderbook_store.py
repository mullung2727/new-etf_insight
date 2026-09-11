"""호가 수집기 저장소 — FID 정규화·스키마·회차 저장·실행 기록 (SPEC §9·§10, T02/T03/T26/T27)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import orderbook_store as store
from scripts.wl_sqlite import connect_ro, connect_rw


def row(ts="09:00:01", ticker="005930", **book):
    base = {"date": "20260911", "ticker": ticker, "venue": "KRX", "ts": ts,
            "recv_ts": "09:00:00.500", "quote_tm": "090000"}
    base.update({c: None for c in store.BOOK_COLUMNS})
    base.update(book)
    return base


class NormalizeTest(unittest.TestCase):
    def test_t02_forty_fids_map_to_ten_levels(self):
        values = {str(f): str(f * 10) for f in range(41, 81)}
        book, invalid = store.normalize(values)
        self.assertEqual(invalid, 0)
        for level in range(1, 11):
            self.assertEqual(book[f"ask{level}_px"], (40 + level) * 10)
            self.assertEqual(book[f"bid{level}_px"], (50 + level) * 10)
            self.assertEqual(book[f"ask{level}_qty"], (60 + level) * 10)
            self.assertEqual(book[f"bid{level}_qty"], (70 + level) * 10)

    def test_totals_and_two_expected_pairs_stay_separate(self):
        book, _ = store.normalize({"121": "900", "125": "800", "23": "+70100", "24": "12",
                                   "291": "-70200", "292": "34", "21": "090100"})
        self.assertEqual((book["ask_total_qty"], book["bid_total_qty"]), (900, 800))
        self.assertEqual((book["exp_px"], book["exp_qty"]), (70100, 12))
        self.assertEqual((book["exp_px_ca"], book["exp_qty_ca"]), (70200, 34))
        self.assertEqual(book["quote_tm"], "090100")

    def test_t03_sign_zero_empty_and_bad_values(self):
        book, invalid = store.normalize({"41": "-70000", "61": "0", "51": "", "71": "abc"})
        self.assertEqual(book["ask1_px"], 70000)          # 부호는 전일대비 표시 — 가격은 절댓값
        self.assertEqual(book["ask1_qty"], 0)             # 정상 0 은 0
        self.assertIsNone(book["bid1_px"])                # 빈 값 NULL
        self.assertIsNone(book["bid1_qty"])               # 파싱 실패 NULL + 카운트
        self.assertIsNone(book["ask2_px"])                # 누락 NULL
        self.assertIsNone(book["quote_tm"])
        self.assertEqual(invalid, 1)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "orderbook.sqlite3"
        with connect_rw(self.db) as con:
            store.ensure_schema(con)

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema_is_wal_with_run_index(self):
        with connect_ro(self.db) as con:
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
        self.assertTrue({"orderbook_snapshot", "orderbook_run", "ix_orderbook_run_date"} <= names)

    def test_t26_same_pk_keeps_one_row_and_runs_stay_separate(self):
        with connect_rw(self.db) as con:
            first = store.start_run(con, date="20260911", started_at="2026-09-11T08:44:00.000+09:00",
                                    mode="morning", note={"reason": None})
            second = store.start_run(con, date="20260911", started_at="2026-09-11T08:50:00.000+09:00",
                                     mode="morning", note={"reason": None})
            store.write_round(con, first, [row(ask1_px=100)], rows_written=0)
            total = store.write_round(con, second, [row(ask1_px=200)], rows_written=0)
            snaps = con.execute("SELECT ask1_px FROM orderbook_snapshot").fetchall()
            runs = con.execute("SELECT run_id, rows_written FROM orderbook_run ORDER BY run_id").fetchall()
        self.assertNotEqual(first, second)
        self.assertEqual(snaps, [(200,)])
        self.assertEqual(total, 1)
        self.assertEqual(runs, [(first, 1), (second, 1)])

    def test_t27_failed_round_rolls_back_everything(self):
        with connect_rw(self.db) as con:
            run_id = store.start_run(con, date="20260911", started_at="2026-09-11T08:44:00.000+09:00",
                                     mode="morning", note={})
            total = store.write_round(con, run_id, [row("09:00:01")], rows_written=0)
            bad = row("09:00:02", ticker="000660", recv_ts=None)          # NOT NULL 위반
            with self.assertRaises(sqlite3.IntegrityError):
                store.write_round(con, run_id, [row("09:00:02"), bad], rows_written=total,
                                  note={"stats": {"late_events": 1}})
            snaps = con.execute("SELECT count(*) FROM orderbook_snapshot").fetchone()[0]
            rows_written, note = con.execute(
                "SELECT rows_written, note FROM orderbook_run WHERE run_id=?", [run_id]).fetchone()
        self.assertEqual(snaps, 1)
        self.assertEqual(rows_written, 1)
        self.assertEqual(note, "{}")

    def test_update_run_writes_note_as_json(self):
        with connect_rw(self.db) as con:
            run_id = store.start_run(con, date="20260911", started_at="s", mode="afternoon", note={})
            store.update_run(con, run_id, ended_at="e", venue="KRX", symbols="005930,000660",
                             source_date="20260911", note={"reason": "window_end"})
            got = con.execute("SELECT ended_at, venue, symbols, source_date, note FROM orderbook_run"
                              " WHERE run_id=?", [run_id]).fetchone()
        self.assertEqual(got, ("e", "KRX", "005930,000660", "20260911", '{"reason": "window_end"}'))


if __name__ == "__main__":
    unittest.main()
