"""투자노트 CRUD — stdlib sqlite3, ORM 없음."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .db import get_conn
from .models import (
    EventCreate,
    Note,
    NoteCreate,
    NoteDetail,
    NoteEvent,
    NoteUpdate,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- notes ---


def create_note(data: NoteCreate) -> Note:
    conn = get_conn()
    uid = uuid.uuid4().hex
    now = _now()
    conn.execute(
        """
        INSERT INTO notes
            (uid, symbol, status, target_price, holding_period,
             buy_reason, memo, user_id, created_at, updated_at)
        VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            data.symbol,
            data.target_price,
            data.holding_period,
            data.buy_reason,
            data.memo,
            data.user_id,
            now,
            now,
        ),
    )
    conn.commit()
    note = get_note(uid)
    assert note is not None
    return note


def list_notes(
    symbol: str | None = None,
    status: str | None = None,
    user_id: str | None = None,
) -> list[Note]:
    conn = get_conn()
    clauses: list[str] = []
    params: list[object] = []
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM notes {where} ORDER BY created_at DESC", params
    ).fetchall()
    return [Note(**dict(r)) for r in rows]


def get_note(uid: str) -> NoteDetail | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM notes WHERE uid = ?", (uid,)).fetchone()
    if row is None:
        return None
    events = conn.execute(
        "SELECT * FROM note_events WHERE note_uid = ? ORDER BY executed_at, id",
        (uid,),
    ).fetchall()
    return NoteDetail(
        **dict(row),
        events=[NoteEvent(**dict(e)) for e in events],
    )


def update_note(uid: str, data: NoteUpdate) -> NoteDetail | None:
    conn = get_conn()
    fields = data.model_dump(exclude_none=True)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [_now(), uid]
        cur = conn.execute(
            f"UPDATE notes SET {sets}, updated_at = ? WHERE uid = ?", params
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    return get_note(uid)


def delete_note(uid: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM notes WHERE uid = ?", (uid,))
    conn.commit()
    return cur.rowcount > 0


# --- events ---


def add_event(note_uid: str, data: EventCreate) -> NoteEvent | None:
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM notes WHERE uid = ?", (note_uid,)
    ).fetchone()
    if exists is None:
        return None
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO note_events
            (note_uid, event_type, price, qty, executed_at, memo, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note_uid,
            data.event_type.value,
            data.price,
            data.qty,
            data.executed_at,
            data.memo,
            now,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM note_events WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return NoteEvent(**dict(row))


def delete_event(note_uid: str, event_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM note_events WHERE id = ? AND note_uid = ?",
        (event_id, note_uid),
    )
    conn.commit()
    return cur.rowcount > 0
