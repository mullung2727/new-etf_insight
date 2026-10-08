"""orderbook_archive.py 단위 테스트 (임시 src·dst, 실제 D: 사용 안 함).

Usage (from etl/):
    PYTHONPATH=. uv run python -m unittest tests.test_orderbook_archive -v
"""
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from scripts.orderbook_archive import main, run

KST = ZoneInfo("Asia/Seoul")

# trade: (rt, code, t, price, qty, cum_vol, ask1, bid1) — 3종목 5행, code 미정렬
TRADE_ROWS = (
    (1759881000000000000, "005930", "090000", 70000, 10, 100, 70100, 69900),
    (1759881001000000000, "005930", "090001", 70100, -5, 105, 70200, 70000),
    (1759881002000000000, "000660", "090002", 150000, 3, 50, 150100, 149900),
    (1759881003000000000, "035720", "090003", 200000, 7, 70, 200100, 199900),
    (1759881004000000000, "000660", "090004", 150100, -2, 52, 150200, 150000),
)

_BOOK_COLS = (
    ["rt", "code", "t"]
    + [f"ap{i}" for i in range(1, 11)]
    + [f"bp{i}" for i in range(1, 11)]
    + [f"aq{i}" for i in range(1, 11)]
    + [f"bq{i}" for i in range(1, 11)]
    + ["ask_tot", "bid_tot"]
)


def _book_row(rt, code, t, base):
    vals = {"rt": rt, "code": code, "t": t}
    for i in range(1, 11):
        vals[f"ap{i}"] = base + i * 100
        vals[f"bp{i}"] = base - i * 100
        vals[f"aq{i}"] = 100 + i
        vals[f"bq{i}"] = 200 + i
    vals["ask_tot"] = 1000
    vals["bid_tot"] = 2000
    return tuple(vals[c] for c in _BOOK_COLS)


BOOK_ROWS = (
    _book_row(1759881000000000000, "005930", "090000", 70000),
    _book_row(1759881001000000000, "000660", "090001", 150000),
    _book_row(1759881002000000000, "035720", "090002", 200000),
    _book_row(1759881003000000000, "005930", "090003", 70100),
)

# gaps: code·rt 컬럼 없음 → 정렬 없음·count 만 검증 경로
GAPS_ROWS = (
    ("wss://localhost:8001", 1759881000000000000, 1759881060000000000),
    ("wss://localhost:8001", 1759882000000000000, 1759882030000000000),
)


def _make_sqlite(path, trade_rows=TRADE_ROWS, book_rows=BOOK_ROWS, gaps_rows=None):
    """테스트용 일 파일 생성. None 인 테이블은 만들지 않는다."""
    path = Path(path)
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    try:
        if trade_rows is not None:
            con.execute(
                "CREATE TABLE trade (rt INTEGER, code TEXT, t TEXT, price INTEGER,"
                " qty INTEGER, cum_vol INTEGER, ask1 INTEGER, bid1 INTEGER)"
            )
            con.executemany("INSERT INTO trade VALUES (?,?,?,?,?,?,?,?)", trade_rows)
        if book_rows is not None:
            defs = ", ".join(f"{c} {'TEXT' if c in ('code', 't') else 'INTEGER'}" for c in _BOOK_COLS)
            con.execute(f"CREATE TABLE book ({defs})")
            con.executemany(f"INSERT INTO book VALUES ({','.join('?' * len(_BOOK_COLS))})", book_rows)
        if gaps_rows is not None:
            con.execute("CREATE TABLE gaps (conn TEXT, start_rt INTEGER, end_rt INTEGER)")
            con.executemany("INSERT INTO gaps VALUES (?,?,?)", gaps_rows)
        con.commit()
    finally:
        con.close()
    return path


def _parquet_count(pq_path):
    import duckdb

    con = duckdb.connect()
    try:
        return con.execute(f"SELECT COUNT(*) FROM read_parquet('{Path(pq_path).as_posix()}')").fetchone()[0]
    finally:
        con.close()


class _ArchiveCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name) / "src"
        self.dst = Path(self._tmp.name) / "dst"
        self.src.mkdir()
        # dst 는 미리 만들지 않음 — 함수가 생성해야 함
        self.notify = mock.Mock()
        self.now = datetime(2026, 10, 9, 16, 10, tzinfo=KST)

    def test_1_ok_archived(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        self.notify.assert_not_called()
        trade_pq = self.dst / "2026" / "10" / "trade_20261008.parquet"
        book_pq = self.dst / "2026" / "10" / "book_20261008.parquet"
        self.assertTrue(trade_pq.is_file())
        self.assertTrue(book_pq.is_file())
        self.assertEqual(_parquet_count(trade_pq), len(TRADE_ROWS))
        self.assertEqual(_parquet_count(book_pq), len(BOOK_ROWS))
        self.assertFalse(p.exists())  # sqlite 삭제
        self.assertEqual(list(self.dst.rglob("*.tmp")), [])
        self.assertIn("20261008 archived trade=5 book=4", buf.getvalue())
        # trade parquet 이 code·rt 정렬 저장됐는지 (저장 순서 그대로 읽음)
        import duckdb

        con = duckdb.connect()
        try:
            rows = con.execute(
                f"SELECT code, rt FROM read_parquet('{trade_pq.as_posix()}')"
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(rows, sorted(rows))

    def test_2_no_gaps_table(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")  # gaps 없음
        rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        self.assertFalse((self.dst / "2026" / "10" / "gaps_20261008.parquet").exists())
        self.assertFalse(p.exists())
        self.notify.assert_not_called()

    def test_3_existing_same_parquet(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        self.assertEqual(run(self.src, self.dst, self.now, self.notify), 0)
        _make_sqlite(p)  # sqlite 다시 생성 (같은 내용)
        trade_pq = self.dst / "2026" / "10" / "trade_20261008.parquet"
        mtime_before = trade_pq.stat().st_mtime_ns
        rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        self.assertFalse(p.exists())  # 검증 통과 → sqlite 삭제
        self.assertEqual(trade_pq.stat().st_mtime_ns, mtime_before)  # 덮어쓰기 없음
        self.notify.assert_not_called()

    def test_4_existing_different_parquet(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        self.assertEqual(run(self.src, self.dst, self.now, self.notify), 0)
        extra = (1759881005000000000, "005930", "090005", 70200, 1, 106, 70300, 70100)
        _make_sqlite(p, trade_rows=TRADE_ROWS + (extra,))  # sqlite 에 1행 추가
        trade_pq = self.dst / "2026" / "10" / "trade_20261008.parquet"
        mtime_before = trade_pq.stat().st_mtime_ns
        size_before = trade_pq.stat().st_size
        self.notify.reset_mock()
        rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 1)
        self.assertTrue(p.exists())  # sqlite 보존
        self.assertEqual(trade_pq.stat().st_mtime_ns, mtime_before)  # 기존 parquet 불변
        self.assertEqual(trade_pq.stat().st_size, size_before)
        self.notify.assert_called_once()
        _, kwargs = self.notify.call_args
        self.assertEqual(kwargs.get("channel"), "batch")

    def test_5_verify_fail_mock(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        with mock.patch(
            "scripts.orderbook_archive.verify_table", return_value=(False, "mocked mismatch")
        ):
            rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 1)
        self.assertTrue(p.exists())  # sqlite 보존
        self.assertEqual(list(self.dst.rglob("*.parquet")), [])  # final 없음
        self.assertEqual(list(self.dst.rglob("*.tmp")), [])  # .tmp 잔재 없음
        self.notify.assert_called_once()

    def test_6_dst_parent_missing(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        dst = Path(self._tmp.name) / "nope" / "orderbook"  # 상위 폴더 없음 = 미연결 모의
        rc = run(self.src, dst, self.now, self.notify)
        self.assertEqual(rc, 1)
        self.assertTrue(p.exists())  # sqlite 보존
        self.assertFalse(dst.exists())
        self.assertFalse((Path(self._tmp.name) / "nope").exists())  # 부모를 만들면 안 됨
        self.notify.assert_called_once()
        self.assertIn("dst_missing", self.notify.call_args[0][0])

    def test_7_dst_full(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        usage = namedtuple("usage", "total used free")(100, 99, 1)
        with mock.patch("scripts.orderbook_archive.shutil.disk_usage", return_value=usage):
            rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 1)
        self.assertTrue(p.exists())  # sqlite 보존
        self.notify.assert_called_once()
        self.assertIn("dst_full", self.notify.call_args[0][0])

    def test_8_recording_skip(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        # 같은 날 15:00 → 녹화 중, 아무것도 안 바뀜
        rc = run(self.src, self.dst, datetime(2026, 10, 8, 15, 0, tzinfo=KST), self.notify)
        self.assertEqual(rc, 0)
        self.assertTrue(p.exists())
        self.assertFalse(self.dst.exists())
        self.assertEqual(list(self.src.iterdir()), [p])
        self.notify.assert_not_called()
        # 같은 날 15:41 → 처리
        rc = run(self.src, self.dst, datetime(2026, 10, 8, 15, 41, tzinfo=KST), self.notify)
        self.assertEqual(rc, 0)
        self.assertFalse(p.exists())
        self.assertTrue((self.dst / "2026" / "10" / "trade_20261008.parquet").is_file())

    def test_9a_backlog_three_days(self):
        for ymd in ("20261005", "20261006", "20261007"):
            gaps = GAPS_ROWS if ymd == "20261006" else None  # gaps(code 없음) 경로 포함
            _make_sqlite(self.src / f"orderbook_raw_{ymd}.sqlite3", gaps_rows=gaps)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertLess(out.index("20261005"), out.index("20261006"))  # 날짜 순
        self.assertLess(out.index("20261006"), out.index("20261007"))
        for ymd in ("20261005", "20261006", "20261007"):
            self.assertFalse((self.src / f"orderbook_raw_{ymd}.sqlite3").exists())
            self.assertTrue((self.dst / "2026" / "10" / f"trade_{ymd}.parquet").is_file())
        gaps_pq = self.dst / "2026" / "10" / "gaps_20261006.parquet"
        self.assertTrue(gaps_pq.is_file())
        self.assertEqual(_parquet_count(gaps_pq), len(GAPS_ROWS))
        self.notify.assert_not_called()

    def test_9b_backlog_warning_on_failure(self):
        for ymd in ("20261005", "20261006", "20261007"):
            _make_sqlite(self.src / f"orderbook_raw_{ymd}.sqlite3")
        dst = Path(self._tmp.name) / "nope" / "orderbook"
        rc = run(self.src, dst, self.now, self.notify)
        self.assertEqual(rc, 1)
        for ymd in ("20261005", "20261006", "20261007"):
            self.assertTrue((self.src / f"orderbook_raw_{ymd}.sqlite3").exists())
        self.notify.assert_called_once()
        self.assertIn("warning", self.notify.call_args[0][0])

    def test_10_name_filter(self):
        bad6 = _make_sqlite(self.src / "orderbook_raw_202610.sqlite3")  # 6자리 → 무시
        bad_date = _make_sqlite(self.src / "orderbook_raw_20261345.sqlite3")  # 없는 날짜 → 무시
        rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        self.assertTrue(bad6.exists())
        self.assertTrue(bad_date.exists())
        self.assertEqual(list(self.dst.rglob("*.parquet")) if self.dst.exists() else [], [])
        self.notify.assert_not_called()

    def test_11_dry_run(self):
        p = _make_sqlite(self.src / "orderbook_raw_20261008.sqlite3")
        with mock.patch("scripts.orderbook_archive.notify") as m_notify:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main(["--src", str(self.src), "--dst", str(self.dst), "--dry-run"])
        self.assertEqual(rc, 0)
        self.assertIn("20261008", buf.getvalue())
        self.assertIn("trade_20261008.parquet", buf.getvalue())
        self.assertTrue(p.exists())  # 삭제 없음
        self.assertFalse(self.dst.exists())  # 쓰기 없음
        m_notify.assert_not_called()  # 알림 없음

    def test_12_chunked_read(self):
        p = _make_sqlite(
            self.src / "orderbook_raw_20261008.sqlite3",
            trade_rows=TRADE_ROWS,
            book_rows=None,
        )
        with mock.patch("scripts.orderbook_archive.READ_CHUNK", 2):
            rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        self.notify.assert_not_called()
        trade_pq = self.dst / "2026" / "10" / "trade_20261008.parquet"
        self.assertTrue(trade_pq.is_file())
        self.assertEqual(_parquet_count(trade_pq), 5)
        self.assertFalse(p.exists())  # sqlite 삭제
        import duckdb

        con = duckdb.connect()
        try:
            rows = con.execute(
                f"SELECT code, rt FROM read_parquet('{trade_pq.as_posix()}')"
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(rows, sorted(rows))

    def test_13_gaps_bigint_precision(self):
        gaps_rows = (
            ("heavy", 1791419280964637101, 1791419280964637999),
            ("light", 1791419280964637103, None),
        )
        p = _make_sqlite(
            self.src / "orderbook_raw_20261008.sqlite3",
            trade_rows=None,
            book_rows=None,
            gaps_rows=gaps_rows,
        )
        rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        self.assertFalse(p.exists())  # sqlite 삭제
        gaps_pq = self.dst / "2026" / "10" / "gaps_20261008.parquet"
        self.assertTrue(gaps_pq.is_file())
        import duckdb

        con = duckdb.connect()
        try:
            typ = {
                r[0]: r[1]
                for r in con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{gaps_pq.as_posix()}')"
                ).fetchall()
            }
            rows = con.execute(
                f"SELECT conn, start_rt, end_rt FROM read_parquet('{gaps_pq.as_posix()}')"
                " ORDER BY conn"
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(typ["start_rt"], "BIGINT")
        self.assertEqual(typ["end_rt"], "BIGINT")
        self.assertEqual(
            rows,
            [
                ("heavy", 1791419280964637101, 1791419280964637999),
                ("light", 1791419280964637103, None),
            ],
        )
        self.assertIsNone(rows[1][2])

    def test_14_chunk_null_first_bigint(self):
        trade_rows = (
            (1759881000000000000, "005930", "090000", 70000, 10, 100, None, 69900),
            (1759881001000000000, "005930", "090001", 70100, -5, 105, 70200, 70000),
        )
        p = _make_sqlite(
            self.src / "orderbook_raw_20261008.sqlite3",
            trade_rows=trade_rows,
            book_rows=None,
        )
        with mock.patch("scripts.orderbook_archive.READ_CHUNK", 1):
            rc = run(self.src, self.dst, self.now, self.notify)
        self.assertEqual(rc, 0)
        self.assertFalse(p.exists())  # sqlite 삭제
        trade_pq = self.dst / "2026" / "10" / "trade_20261008.parquet"
        self.assertTrue(trade_pq.is_file())
        self.assertEqual(_parquet_count(trade_pq), 2)
        import duckdb

        con = duckdb.connect()
        try:
            typ = {
                r[0]: r[1]
                for r in con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{trade_pq.as_posix()}')"
                ).fetchall()
            }
            rows = con.execute(
                f"SELECT rt, ask1 FROM read_parquet('{trade_pq.as_posix()}')"
                " ORDER BY rt"
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(typ["ask1"], "BIGINT")
        self.assertEqual(
            rows,
            [
                (1759881000000000000, None),
                (1759881001000000000, 70200),
            ],
        )
        self.assertIsNone(rows[0][1])


if __name__ == "__main__":
    unittest.main()
