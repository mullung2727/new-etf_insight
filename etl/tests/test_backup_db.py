"""backup_db.py 단위 테스트 (임시 가짜 프로젝트 루트·백업 루트).

Usage (from etl/):
    PYTHONPATH=. uv run python -m unittest tests.test_backup_db -v
"""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.backup_db import (
    BACKUP_DIRNAME,
    backup_duckdb,
    backup_file,
    backup_sqlite,
    find_backup_root,
    is_fresh,
    iter_sources,
    main,
    pick_slot,
    run,
)


class _BackupCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.dest = Path(self._tmp.name) / "bak"
        self.root.mkdir()
        self.dest.mkdir()

    def _write(self, rel, data=b"x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def _sqlite(self, rel, rows=(("a", 1), ("b", 2))):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
        con = sqlite3.connect(p)
        con.execute("CREATE TABLE t (k TEXT, v INTEGER)")
        con.executemany("INSERT INTO t VALUES (?, ?)", rows)
        con.commit()
        con.close()
        return p

    def _duckdb(self, rel):
        import duckdb

        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            p.unlink()
        con = duckdb.connect(str(p))
        con.execute("CREATE TABLE t (k VARCHAR, v INTEGER)")
        con.execute("INSERT INTO t VALUES ('a', 1)")
        con.close()
        return p


class TestIterSources(_BackupCase):
    def test_include_and_exclude(self):
        keep = [
            "etl/db/orderbook_raw/orderbook_raw_202610.sqlite3",
            "broker/notes.db",
            "research/private/a/b.json",
            ".env",
        ]
        drop = [
            "etl/db/x.sqlite3-wal",
            "etl/db/a.duckdb.bak-x",
            "etl/db/_bak_del_y",
            "etl/db/orderbook.lock",
            "etl/db/corpcode.zip",
            "broker/.venv/z.db",
            "etl/db/__pycache__/q.pyc",
            "broker/readme.txt",
        ]
        for rel in keep + drop:
            self._write(rel)
        got = {p.relative_to(self.root).as_posix() for p in iter_sources(self.root)}
        for rel in keep:
            self.assertIn(rel, got)
        for rel in drop:
            self.assertNotIn(rel, got)


class TestSqliteBackup(_BackupCase):
    def test_rows_copied_and_second_run_skipped(self):
        self._sqlite("etl/db/a.sqlite3")
        r1 = run(self.root, self.dest)
        self.assertEqual(r1["failed"], [])
        self.assertIn("etl/db/a.sqlite3", r1["copied"])
        con = sqlite3.connect(self.dest / "etl/db/a.sqlite3")
        try:
            rows = con.execute("SELECT k, v FROM t ORDER BY k").fetchall()
        finally:
            con.close()
        self.assertEqual(rows, [("a", 1), ("b", 2)])
        r2 = run(self.root, self.dest)
        self.assertEqual(r2["failed"], [])
        self.assertEqual(r2["copied"], [])
        self.assertEqual(r2["skipped"], 1)

    def test_wal_newer_than_dst_is_not_fresh(self):
        self._sqlite("etl/db/a.sqlite3")
        r1 = run(self.root, self.dest)
        self.assertEqual(r1["failed"], [])
        src = self.root / "etl/db/a.sqlite3"
        dst = self.dest / "etl/db/a.sqlite3"
        wal = src.parent / (src.name + "-wal")
        wal.write_bytes(b"wal")
        future = dst.stat().st_mtime + 10
        os.utime(wal, (future, future))
        self.assertFalse(is_fresh(src, dst))


class TestSqliteCorrupt(_BackupCase):
    def test_corrupt_source_keeps_existing_dst(self):
        dst = self.dest / "etl/db/c.sqlite3"
        dst.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(dst)
        con.execute("CREATE TABLE t (k TEXT)")
        con.execute("INSERT INTO t VALUES ('good')")
        con.commit()
        con.close()
        before = dst.read_bytes()
        src = self._write(
            "etl/db/c.sqlite3", b"SQLite format 3\x00" + b"\x00" * 100 + b"garbage" * 100
        )
        newer = dst.stat().st_mtime + 10
        os.utime(src, (newer, newer))
        result = run(self.root, self.dest)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0][0], "etl/db/c.sqlite3")
        self.assertEqual(dst.read_bytes(), before)

    def test_quick_check_fail_keeps_dst(self):
        src = self._sqlite("etl/db/q.sqlite3")
        dst = self.dest / "etl/db/q.sqlite3"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"old-backup")
        newer = dst.stat().st_mtime + 10
        os.utime(src, (newer, newer))
        real_connect = sqlite3.connect

        class _QuickCheckFailConnection:
            def __init__(self, con):
                self._con = con

            def backup(self, target, *args, **kwargs):
                if isinstance(target, _QuickCheckFailConnection):
                    target = target._con
                return self._con.backup(target, *args, **kwargs)

            def execute(self, sql, *args, **kwargs):
                if "quick_check" in sql:
                    m = mock.Mock()
                    m.fetchall.return_value = [("corrupt",)]
                    return m
                return self._con.execute(sql, *args, **kwargs)

            def close(self):
                return self._con.close()

        def fake_connect(*args, **kwargs):
            return _QuickCheckFailConnection(real_connect(*args, **kwargs))

        with mock.patch("scripts.backup_db.sqlite3.connect", new=fake_connect):
            result = run(self.root, self.dest)
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn("quick_check", result["failed"][0][1])
        self.assertEqual(dst.read_bytes(), b"old-backup")
        self.assertFalse(dst.with_name(dst.name + ".tmp").exists())


class TestDuckdbBackup(_BackupCase):
    def test_copy_and_query(self):
        import duckdb

        self._duckdb("etl/db/d.duckdb")
        result = run(self.root, self.dest)
        self.assertEqual(result["failed"], [])
        con = duckdb.connect(str(self.dest / "etl/db/d.duckdb"), read_only=True)
        try:
            rows = con.execute("SELECT k, v FROM t").fetchall()
        finally:
            con.close()
        self.assertEqual(rows, [("a", 1)])

    def test_wal_blocks_backup(self):
        self._duckdb("etl/db/w.duckdb")
        self._write("etl/db/w.duckdb.wal", b"wal")
        result = run(self.root, self.dest)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0][0], "etl/db/w.duckdb")
        self.assertIn("wal", result["failed"][0][1])
        self.assertFalse((self.dest / "etl/db/w.duckdb").exists())


class TestPlainFile(_BackupCase):
    def test_copy_and_skip(self):
        self._write("research/private/a/b.json", '{"k": 1}')
        r1 = run(self.root, self.dest)
        self.assertEqual(r1["failed"], [])
        self.assertEqual(
            (self.dest / "research/private/a/b.json").read_bytes(), b'{"k": 1}'
        )
        r2 = run(self.root, self.dest)
        self.assertEqual(r2["copied"], [])
        self.assertEqual(r2["skipped"], 1)


class TestFindBackupRoot(unittest.TestCase):
    def test_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "drv"
            base.mkdir()
            (base / BACKUP_DIRNAME).mkdir()
            other = Path(tmp) / "empty"
            other.mkdir()
            self.assertEqual(
                find_backup_root(candidates=[other, base]), base / BACKUP_DIRNAME
            )
            self.assertIsNone(find_backup_root(candidates=[other]))


class TestMainDriveMissing(unittest.TestCase):
    def test_no_drive_notifies_once(self):
        with mock.patch("scripts.backup_db.find_backup_root", return_value=None):
            with mock.patch("scripts.backup_db.notify", return_value=True) as m_notify:
                rc = main()
        self.assertEqual(rc, 1)
        m_notify.assert_called_once()
        self.assertIn("외장하드", m_notify.call_args[0][0])


class TestPickSlot(unittest.TestCase):
    def test_no_latest_is_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(pick_slot(Path(tmp)), "A")

    def test_a_to_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "latest.txt").write_text("A", encoding="utf-8")
            self.assertEqual(pick_slot(Path(tmp)), "B")

    def test_b_to_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "latest.txt").write_text("B", encoding="utf-8")
            self.assertEqual(pick_slot(Path(tmp)), "A")

    def test_garbage_is_a(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "latest.txt").write_text("zzz", encoding="utf-8")
            self.assertEqual(pick_slot(Path(tmp)), "A")


class TestMainSlots(_BackupCase):
    def _patched_main(self):
        return (
            mock.patch("scripts.backup_db.find_backup_root", return_value=self.dest),
            mock.patch("scripts.backup_db.notify", return_value=True),
            mock.patch("scripts.backup_db.PROJECT_ROOT", self.root),
        )

    def test_success_alternates_slots(self):
        self._write("research/private/a/b.json", '{"k": 1}')
        p_root, p_notify, p_proj = self._patched_main()
        with p_root:
            with p_notify:
                with p_proj:
                    rc1 = main()
        self.assertEqual(rc1, 0)
        self.assertTrue((self.dest / "slot_A/research/private/a/b.json").is_file())
        self.assertEqual((self.dest / "latest.txt").read_text(encoding="utf-8"), "A")
        p_root, p_notify, p_proj = self._patched_main()
        with p_root:
            with p_notify:
                with p_proj:
                    rc2 = main()
        self.assertEqual(rc2, 0)
        self.assertTrue((self.dest / "slot_B/research/private/a/b.json").is_file())
        self.assertEqual((self.dest / "latest.txt").read_text(encoding="utf-8"), "B")

    def test_failure_keeps_latest(self):
        (self.dest / "latest.txt").write_text("A", encoding="utf-8")
        result = {"copied": [], "skipped": 0, "failed": [("x", "boom")], "bytes": 0}
        with mock.patch("scripts.backup_db.find_backup_root", return_value=self.dest):
            with mock.patch(
                "scripts.backup_db.notify", return_value=True
            ) as m_notify:
                with mock.patch("scripts.backup_db.PROJECT_ROOT", self.root):
                    with mock.patch("scripts.backup_db.run", return_value=result):
                        rc = main()
        self.assertEqual(rc, 1)
        self.assertEqual((self.dest / "latest.txt").read_text(encoding="utf-8"), "A")
        self.assertIn("최신 유지", m_notify.call_args[0][0])


class TestRowCountMismatch(_BackupCase):
    def test_sqlite_mismatch_keeps_dst(self):
        src = self._sqlite("etl/db/m.sqlite3")
        dst = self.dest / "etl/db/m.sqlite3"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"old-backup")
        with mock.patch(
            "scripts.backup_db.table_counts_sqlite",
            side_effect=[{"t": 2}, {"t": 1}],
        ):
            with self.assertRaisesRegex(RuntimeError, "행 수 불일치"):
                backup_sqlite(src, dst)
        self.assertEqual(dst.read_bytes(), b"old-backup")
        self.assertFalse(dst.with_name(dst.name + ".tmp").exists())


class TestTmpCleanupOnFailure(_BackupCase):
    def _failing_copy2(self, exc):
        def fake_copy2(src, dst, *args, **kwargs):
            Path(dst).write_bytes(b"partial")
            raise exc

        return fake_copy2

    def test_backup_file_copy_fail_cleans_tmp(self):
        src = self._write("research/private/f.json", '{"k": 1}')
        dst = self.dest / "research/private/f.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"old-backup")
        tmp = dst.with_name(dst.name + ".tmp")
        with mock.patch(
            "scripts.backup_db.shutil.copy2",
            new=self._failing_copy2(OSError("copy boom")),
        ):
            with self.assertRaises(OSError):
                backup_file(src, dst)
        self.assertFalse(tmp.exists())
        self.assertEqual(dst.read_bytes(), b"old-backup")

    def test_backup_duckdb_copy_fail_cleans_tmp(self):
        src = self._duckdb("etl/db/d.duckdb")
        dst = self.dest / "etl/db/d.duckdb"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"old-backup")
        tmp = dst.with_name(dst.name + ".tmp")
        with mock.patch(
            "scripts.backup_db.shutil.copy2",
            new=self._failing_copy2(OSError("copy boom")),
        ):
            with self.assertRaises(OSError):
                backup_duckdb(src, dst)
        self.assertFalse(tmp.exists())
        self.assertEqual(dst.read_bytes(), b"old-backup")

    def test_backup_sqlite_second_connect_fail_cleans_tmp(self):
        src = self._sqlite("etl/db/s.sqlite3")
        dst = self.dest / "etl/db/s.sqlite3"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"old-backup")
        tmp = dst.with_name(dst.name + ".tmp")
        real_connect = sqlite3.connect

        class _CloseTrack:
            def __init__(self, con):
                self._con = con
                self.closed = False

            def close(self):
                self.closed = True
                return self._con.close()

            def __getattr__(self, name):
                return getattr(self._con, name)

        for exc in (OSError("open boom"), sqlite3.OperationalError("open boom")):
            with self.subTest(exc=type(exc).__name__):
                tmp.write_bytes(b"stale")
                calls = {"n": 0}
                tracked = {}

                def fake_connect(*args, _exc=exc, **kwargs):
                    calls["n"] += 1
                    if calls["n"] == 2:
                        tmp.write_bytes(b"partial")
                        raise _exc
                    con = _CloseTrack(real_connect(*args, **kwargs))
                    tracked["con"] = con
                    return con

                with mock.patch(
                    "scripts.backup_db.sqlite3.connect", new=fake_connect
                ):
                    with self.assertRaises(type(exc)):
                        backup_sqlite(src, dst)
                self.assertFalse(tmp.exists())
                self.assertEqual(dst.read_bytes(), b"old-backup")
                self.assertTrue(tracked["con"].closed)
