"""투자노트 종목명(name) 저장·마이그레이션 테스트.

broker는 pytest가 없으므로 stdlib unittest로 돌린다.
    .venv/Scripts/python.exe -m unittest test_notes_name
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from notes import db, store
from notes.models import NoteCreate


class _Base(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = Path(path)
        os.environ["NOTES_DB_PATH"] = str(self.db)
        db._conn = None
        db.NOTES_DB_PATH = self.db
        # 종목명 조회를 가짜로 — 키움 붙지 않고 테스트.
        self._orig_resolve = store.resolve_name
        store.resolve_name = lambda symbol: {"005930": "삼성전자"}.get(symbol)

    def tearDown(self):
        store.resolve_name = self._orig_resolve
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        os.environ.pop("NOTES_DB_PATH", None)
        self.db.unlink(missing_ok=True)


class CreateFillsName(_Base):
    def test_create_note_stores_resolved_name(self):
        db.init()
        note = store.create_note(NoteCreate(symbol="005930"))
        self.assertEqual(note.name, "삼성전자")
        # 저장 후 재조회에도 유지.
        self.assertEqual(store.get_note(note.uid).name, "삼성전자")

    def test_unknown_symbol_saves_null_name(self):
        db.init()
        note = store.create_note(NoteCreate(symbol="999999"))
        self.assertIsNone(note.name)


class MigrateLegacyDB(unittest.TestCase):
    def test_migrate_adds_name_column_to_old_notes(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        p = Path(path)
        # name 컬럼 없는 구버전 notes 테이블을 손으로 만든다.
        c = sqlite3.connect(str(p))
        c.execute(
            "CREATE TABLE notes (uid TEXT PRIMARY KEY, symbol TEXT NOT NULL, "
            "status TEXT, target_price INTEGER, holding_period TEXT, "
            "buy_reason TEXT, memo TEXT, user_id TEXT, created_at TEXT, updated_at TEXT)"
        )
        c.commit()
        c.close()
        try:
            db._conn = None
            db.NOTES_DB_PATH = p
            conn = db.init()  # init → _migrate 실행
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(notes)")}
            self.assertIn("name", cols)
        finally:
            if db._conn is not None:
                db._conn.close()
                db._conn = None
            p.unlink(missing_ok=True)


class ResolveNameGraceful(unittest.TestCase):
    def test_resolve_name_swallows_kiwoom_errors(self):
        # 키움 조회가 터져도 예외 전파 없이 None (노트 저장을 막지 않는다).
        from kiwoom import quotes

        orig = quotes.get_quote
        quotes.get_quote = lambda symbol: (_ for _ in ()).throw(RuntimeError("kiwoom down"))
        try:
            self.assertIsNone(store.resolve_name("005930"))
        finally:
            quotes.get_quote = orig


if __name__ == "__main__":
    unittest.main()
