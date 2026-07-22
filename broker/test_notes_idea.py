"""투자노트 `idea` 상태 + 진입가(entry_price) 컬럼 테스트.

broker는 pytest가 없으므로 stdlib unittest로 돌린다.
    .venv/Scripts/python.exe -m unittest test_notes_idea
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from notes import db, store
from notes.models import NoteCreate, NoteStatus, NoteUpdate

_LEGACY_NOTES_DDL = (
    "CREATE TABLE notes (uid TEXT PRIMARY KEY, symbol TEXT NOT NULL, name TEXT, "
    "status TEXT NOT NULL DEFAULT 'open', target_price INTEGER, holding_period TEXT, "
    "buy_reason TEXT, memo TEXT, user_id TEXT NOT NULL DEFAULT 'local', "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
)
_LEGACY_EVENTS_DDL = (
    "CREATE TABLE note_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "note_uid TEXT NOT NULL REFERENCES notes(uid) ON DELETE CASCADE, "
    "event_type TEXT NOT NULL, price INTEGER NOT NULL, qty INTEGER NOT NULL, "
    "executed_at TEXT NOT NULL, memo TEXT, order_no TEXT, created_at TEXT NOT NULL)"
)


class _TempDB(unittest.TestCase):
    """빈 임시 DB + 종목명 조회 스텁."""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = Path(path)
        os.environ["NOTES_DB_PATH"] = str(self.db)
        db._conn = None
        db.NOTES_DB_PATH = self.db
        self._orig_resolve = store.resolve_name
        store.resolve_name = lambda symbol: None

    def tearDown(self):
        store.resolve_name = self._orig_resolve
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        os.environ.pop("NOTES_DB_PATH", None)
        self.db.unlink(missing_ok=True)


class CreateStartsAsIdea(_TempDB):
    def test_manual_note_starts_in_idea(self):
        db.init()
        note = store.create_note(NoteCreate(symbol="005930"))
        self.assertEqual(note.status, NoteStatus.idea)

    def test_entry_price_roundtrip(self):
        db.init()
        note = store.create_note(NoteCreate(symbol="005930", entry_price=68000))
        self.assertEqual(note.entry_price, 68000)
        self.assertEqual(store.get_note(note.uid).entry_price, 68000)

    def test_alert_fields_default(self):
        db.init()
        note = store.create_note(NoteCreate(symbol="005930"))
        self.assertEqual(note.alert_off, 0)
        self.assertIsNone(note.alerted_on)

    def test_update_sets_entry_price(self):
        db.init()
        note = store.create_note(NoteCreate(symbol="005930"))
        updated = store.update_note(note.uid, NoteUpdate(entry_price=70000))
        self.assertEqual(updated.entry_price, 70000)


class MigrateLegacyDB(unittest.TestCase):
    """구버전 DB(entry_price 없음)를 열었을 때의 ALTER + 백필."""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = Path(path)
        c = sqlite3.connect(str(self.db))
        c.execute(_LEGACY_NOTES_DDL)
        c.execute(_LEGACY_EVENTS_DDL)
        # 이벤트 0건 open 노트 = 실제로는 조사 단계 → idea로 내려가야 한다.
        c.execute(
            "INSERT INTO notes (uid, symbol, status, user_id, created_at, updated_at) "
            "VALUES ('u_bare', '005930', 'open', 'local', 't', 't')"
        )
        # 이벤트가 있는 open 노트 = 실제 보유 → 그대로 open.
        c.execute(
            "INSERT INTO notes (uid, symbol, status, user_id, created_at, updated_at) "
            "VALUES ('u_held', '000660', 'open', 'local', 't', 't')"
        )
        c.execute(
            "INSERT INTO note_events (note_uid, event_type, price, qty, executed_at, created_at) "
            "VALUES ('u_held', 'buy', 1000, 10, '2026-01-02', 't')"
        )
        c.commit()
        c.close()
        db._conn = None
        db.NOTES_DB_PATH = self.db

    def tearDown(self):
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        self.db.unlink(missing_ok=True)

    def test_adds_columns(self):
        conn = db.init()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(notes)")}
        self.assertIn("entry_price", cols)
        self.assertIn("alert_off", cols)
        self.assertIn("alerted_on", cols)

    def test_backfills_eventless_open_note_to_idea(self):
        conn = db.init()
        row = conn.execute("SELECT status FROM notes WHERE uid = 'u_bare'").fetchone()
        self.assertEqual(row["status"], "idea")

    def test_keeps_open_note_that_has_events(self):
        conn = db.init()
        row = conn.execute("SELECT status FROM notes WHERE uid = 'u_held'").fetchone()
        self.assertEqual(row["status"], "open")

    def test_backfill_does_not_rerun_on_already_migrated_db(self):
        # 1회차 마이그레이션 후, 사용자가 의도적으로 open으로 되돌린 노트가
        # 재기동(2회차 init)에서 다시 idea로 끌려가면 안 된다.
        conn = db.init()
        conn.execute("UPDATE notes SET status = 'open' WHERE uid = 'u_bare'")
        conn.commit()
        conn.close()
        db._conn = None
        conn = db.init()
        row = conn.execute("SELECT status FROM notes WHERE uid = 'u_bare'").fetchone()
        self.assertEqual(row["status"], "open")


class AlertCandidates(_TempDB):
    """list_idea_alert_candidates 필터 — idea + entry_price + alert_off=0 + 오늘 미발송."""

    def _mk(self, symbol, **cols):
        db.init()
        note = store.create_note(NoteCreate(symbol=symbol, entry_price=cols.pop("entry_price", 1000)))
        if cols:
            conn = db.get_conn()
            sets = ", ".join(f"{k} = ?" for k in cols)
            conn.execute(f"UPDATE notes SET {sets} WHERE uid = ?", [*cols.values(), note.uid])
            conn.commit()
        return note.uid

    def test_includes_plain_idea_with_entry_price(self):
        uid = self._mk("005930")
        cands = store.list_idea_alert_candidates("20260722")
        self.assertEqual([c.uid for c in cands], [uid])

    def test_excludes_null_entry_price(self):
        db.init()
        store.create_note(NoteCreate(symbol="005930"))  # entry_price=None
        self.assertEqual(store.list_idea_alert_candidates("20260722"), [])

    def test_excludes_muted(self):
        self._mk("005930", alert_off=1, alerted_on="20260721")
        self.assertEqual(store.list_idea_alert_candidates("20260722"), [])

    def test_excludes_already_alerted_today(self):
        self._mk("005930", alerted_on="20260722")
        self.assertEqual(store.list_idea_alert_candidates("20260722"), [])

    def test_includes_alerted_on_previous_day(self):
        uid = self._mk("005930", alerted_on="20260721")
        cands = store.list_idea_alert_candidates("20260722")
        self.assertEqual([c.uid for c in cands], [uid])

    def test_excludes_non_idea(self):
        uid = self._mk("005930")
        store.update_note(uid, NoteUpdate(status=NoteStatus.open))
        self.assertEqual(store.list_idea_alert_candidates("20260722"), [])

    def test_mark_alerted_sets_date(self):
        uid = self._mk("005930")
        store.mark_alerted(uid, "20260722")
        self.assertEqual(store.get_note(uid).alerted_on, "20260722")


class MuteGuard(unittest.TestCase):
    """alert_off=1은 도달 이력(alerted_on)이 있을 때만 허용. MCP LLM 오작동 대비 서버 가드."""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self.db = Path(path)
        db.NOTES_DB_PATH = self.db
        db._conn = None
        db.init()
        self._orig_resolve = store.resolve_name
        store.resolve_name = lambda symbol: None

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import notes as notes_router

        app = FastAPI()
        app.include_router(notes_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        store.resolve_name = self._orig_resolve
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        self.db.unlink(missing_ok=True)

    def test_mute_rejected_without_hit(self):
        note = store.create_note(NoteCreate(symbol="005930", entry_price=1000))
        r = self.client.patch(f"/notes/{note.uid}", json={"alert_off": 1})
        self.assertEqual(r.status_code, 400)
        # 상태는 안 바뀐다.
        self.assertEqual(store.get_note(note.uid).alert_off, 0)

    def test_mute_allowed_after_hit(self):
        note = store.create_note(NoteCreate(symbol="005930", entry_price=1000))
        store.mark_alerted(note.uid, "20260722")
        r = self.client.patch(f"/notes/{note.uid}", json={"alert_off": 1})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(store.get_note(note.uid).alert_off, 1)

    def test_unmute_always_allowed(self):
        note = store.create_note(NoteCreate(symbol="005930", entry_price=1000))
        r = self.client.patch(f"/notes/{note.uid}", json={"alert_off": 0})
        self.assertEqual(r.status_code, 200)


class DuplicateGuard(unittest.TestCase):
    """수동 노트 생성은 종목당 활성 노트(idea/open/partial)가 있으면 거부(409).

    체결 자동연결(autolink.create_note)은 store 직접 호출이라 이 가드에 안 걸린다.
    """

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        self.db = Path(path)
        db.NOTES_DB_PATH = self.db
        db._conn = None
        db.init()
        self._orig_resolve = store.resolve_name
        store.resolve_name = lambda symbol: None

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import notes as notes_router

        app = FastAPI()
        app.include_router(notes_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        store.resolve_name = self._orig_resolve
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        self.db.unlink(missing_ok=True)

    def test_active_note_for_symbol_finds_idea(self):
        note = store.create_note(NoteCreate(symbol="005930"))
        found = store.active_note_for_symbol("005930")
        self.assertEqual(found.uid, note.uid)

    def test_active_note_for_symbol_ignores_closed(self):
        note = store.create_note(NoteCreate(symbol="005930"))
        store.update_note(note.uid, NoteUpdate(status=NoteStatus.closed))
        self.assertIsNone(store.active_note_for_symbol("005930"))

    def test_active_note_normalizes_prefix(self):
        note = store.create_note(NoteCreate(symbol="005930"))
        self.assertEqual(store.active_note_for_symbol("A005930").uid, note.uid)

    def test_create_rejected_when_active_exists(self):
        store.create_note(NoteCreate(symbol="005930"))
        r = self.client.post("/notes", json={"symbol": "005930"})
        self.assertEqual(r.status_code, 409)
        # 새 노트가 만들어지지 않는다.
        self.assertEqual(len(store.list_notes(symbol="005930")), 1)

    def test_create_allowed_when_only_closed(self):
        note = store.create_note(NoteCreate(symbol="005930"))
        store.update_note(note.uid, NoteUpdate(status=NoteStatus.closed))
        r = self.client.post("/notes", json={"symbol": "005930"})
        self.assertEqual(r.status_code, 200)

    def test_create_allowed_for_new_symbol(self):
        store.create_note(NoteCreate(symbol="005930"))
        r = self.client.post("/notes", json={"symbol": "000660"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
