"""투자노트 CRUD — stdlib sqlite3, ORM 없음."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from . import pnl as pnl_calc
from .db import get_conn
from .models import (
    EventCreate,
    Note,
    NoteCreate,
    NoteDetail,
    NoteEvent,
    NotePnlSummaryItem,
    NoteUpdate,
)

_TICKER_PREFIX = re.compile(r"^\D+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_symbol(symbol: str) -> str:
    return _TICKER_PREFIX.sub("", str(symbol or "").strip())


def resolve_name(symbol: str) -> str | None:
    """종목코드 → 종목명. 키움 ka10001(stk_nm). 조회 실패 시 None(이름 없이 저장)."""
    try:
        from kiwoom.quotes import get_quote

        name = str(get_quote(symbol).raw.get("stk_nm", "") or "").strip()
        return name or None
    except Exception:  # 키움 다운·토큰만료 등 — 이름은 부가정보라 저장을 막지 않는다
        return None


# --- notes ---


def create_note(data: NoteCreate) -> Note:
    conn = get_conn()
    uid = uuid.uuid4().hex
    now = _now()
    symbol = normalize_symbol(data.symbol)
    name = resolve_name(symbol)
    conn.execute(
        """
        INSERT INTO notes
            (uid, symbol, name, status, target_price, entry_price, holding_period,
             buy_reason, memo, user_id, created_at, updated_at)
        VALUES (?, ?, ?, 'idea', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            symbol,
            name,
            data.target_price,
            data.entry_price,
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
    normalized_symbol = normalize_symbol(symbol) if symbol else None
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
    notes = [Note(**dict(r)) for r in rows]
    if normalized_symbol:
        notes = [n for n in notes if normalize_symbol(n.symbol) == normalized_symbol]
    return notes


_ACTIVE_STATUSES = {"idea", "open", "partial"}


def active_note_for_symbol(symbol: str) -> Note | None:
    """해당 종목의 활성(idea/open/partial) 노트 1건. 없으면 None.

    종목당 노트 1개 원칙의 '수동 생성' 가드용. closed만 있으면(재관심) None을
    돌려 새 노트를 허용한다. 자동연결(autolink)은 store.create_note를 직접 써서
    이 가드를 거치지 않는다.
    """
    symbol = normalize_symbol(symbol)
    if not symbol:
        return None
    for n in list_notes(symbol=symbol):
        if n.status.value in _ACTIVE_STATUSES:
            return n
    return None


def merge_notes_by_symbol(symbol: str) -> str | None:
    """Merge duplicate notes for one symbol into the oldest note."""
    symbol = normalize_symbol(symbol)
    if not symbol:
        return None
    notes = sorted(list_notes(symbol=symbol), key=lambda n: (n.created_at, n.uid))
    if not notes:
        return None
    keep = notes[0]
    duplicates = [n.uid for n in notes[1:]]
    if not duplicates:
        if keep.symbol != symbol:
            conn = get_conn()
            conn.execute(
                "UPDATE notes SET symbol = ?, updated_at = ? WHERE uid = ?",
                (symbol, _now(), keep.uid),
            )
            conn.commit()
        return keep.uid

    conn = get_conn()
    placeholders = ", ".join("?" for _ in duplicates)
    conn.execute(
        f"UPDATE note_events SET note_uid = ? WHERE note_uid IN ({placeholders})",
        [keep.uid, *duplicates],
    )
    conn.execute(
        f"DELETE FROM notes WHERE uid IN ({placeholders})",
        duplicates,
    )
    conn.execute(
        "UPDATE notes SET symbol = ?, updated_at = ? WHERE uid = ?",
        (symbol, _now(), keep.uid),
    )
    conn.commit()
    return keep.uid


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


def list_idea_alert_candidates(today: str) -> list[Note]:
    """진입가 도달 감시 대상 idea 노트. today=YYYYMMDD.

    idea 상태 + entry_price 있음 + 알림 안 끔 + 오늘 아직 미발송인 것만.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM notes
        WHERE status = 'idea'
          AND entry_price IS NOT NULL
          AND alert_off = 0
          AND (alerted_on IS NULL OR alerted_on != ?)
        """,
        (today,),
    ).fetchall()
    return [Note(**dict(r)) for r in rows]


def mark_alerted(uid: str, today: str) -> None:
    """진입가 도달 알림을 오늘 보냈다고 기록. 하루 1회 발송 멱등 키."""
    conn = get_conn()
    conn.execute(
        "UPDATE notes SET alerted_on = ?, updated_at = ? WHERE uid = ?",
        (today, _now(), uid),
    )
    conn.commit()


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


def upsert_event(
    note_uid: str,
    order_no: str,
    event_type: str,
    price: int,
    qty: int,
    executed_at: str,
) -> NoteEvent:
    """order_no 기준 멱등 upsert (자동연결 전용).

    kt00007은 주문번호 단위 누적 집계라 부분→전량 체결 시 같은 order_no의 qty가
    커진다. order_no 부분 유니크 인덱스로 충돌 시 qty/price/type/시각을 갱신한다.
    수동 입력 이벤트(order_no NULL)와는 충돌하지 않는다.
    """
    conn = get_conn()
    now = _now()
    conn.execute(
        """
        INSERT INTO note_events
            (note_uid, event_type, price, qty, executed_at, order_no, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(order_no) WHERE order_no IS NOT NULL DO UPDATE SET
            event_type  = excluded.event_type,
            price       = excluded.price,
            qty         = excluded.qty,
            executed_at = excluded.executed_at
        """,
        (note_uid, event_type, price, qty, executed_at, order_no, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM note_events WHERE order_no = ?", (order_no,)
    ).fetchone()
    return NoteEvent(**dict(row))


def find_note_uid_by_order_no(order_no: str) -> str | None:
    """이 order_no를 이미 가진 이벤트의 노트 uid. 없으면 None.

    자동연결 멱등성의 핵심: order_no가 어느 노트(닫힌 노트 포함)에 속하는지
    먼저 확인해 같은 노트에 재반영한다(종목 기준 _active_note보다 우선).
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT note_uid FROM note_events WHERE order_no = ?", (order_no,)
    ).fetchone()
    return row["note_uid"] if row else None


def update_event_type(event_id: int, event_type: str) -> None:
    """이벤트 분류(buy/add_buy/partial_sell/sell)만 갱신한다(시간순 재계산용)."""
    conn = get_conn()
    conn.execute(
        "UPDATE note_events SET event_type = ? WHERE id = ?",
        (event_type, event_id),
    )
    conn.commit()


def delete_event(note_uid: str, event_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute(
        "DELETE FROM note_events WHERE id = ? AND note_uid = ?",
        (event_id, note_uid),
    )
    conn.commit()
    return cur.rowcount > 0


# --- 손익 (pnl.py 계산 + 현재가 조회 연결) ---
#
# 현재가 조회 필요 여부는 note.status가 아니라 이벤트에서 직접 계산한
# remaining_qty로 판단한다. status는 autolink._reclassify가 갱신하는데,
# 수동 add_note_event 경로는 이걸 안 태워 status가 낡을 수 있어서다.


def _current_price(symbol: str) -> int | None:
    try:
        from kiwoom.quotes import get_quote

        return get_quote(symbol).price
    except Exception:  # 조회 실패해도 gross 손익 없이 죽지 않게
        return None


def get_note_with_pnl(uid: str) -> NoteDetail | None:
    """노트 상세 + 손익. 보유수량이 남아있으면 현재가를 1회 조회한다."""
    note = get_note(uid)
    if note is None:
        return None
    provisional = pnl_calc.compute(note.events, None)
    if provisional.remaining_qty > 0:
        note.pnl = pnl_calc.compute(note.events, _current_price(note.symbol))
    else:
        note.pnl = provisional
    return note


def list_notes_with_pnl(
    symbol: str | None = None,
    status: str | None = None,
    user_id: str | None = None,
) -> list[NotePnlSummaryItem]:
    """목록 손익 배치 계산. 보유수량 남은 노트들 종목만 모아 시세 1콜로 조회한다."""
    from kiwoom.quotes import get_watchlist_quotes

    notes = list_notes(symbol=symbol, status=status, user_id=user_id)
    details = {n.uid: d for n in notes if (d := get_note(n.uid)) is not None}
    provisional = {uid: pnl_calc.compute(d.events, None) for uid, d in details.items()}

    live_symbols = sorted(
        {details[uid].symbol for uid, p in provisional.items() if p.remaining_qty > 0}
    )
    price_map: dict[str, int | None] = {}
    if live_symbols:
        try:
            for row in get_watchlist_quotes(live_symbols):
                price_map[row["stk_cd"]] = row.get("cur_prc")
        except Exception:  # 시세 조회 실패해도 gross 없이 계속(needs_price로 표시)
            pass

    out: list[NotePnlSummaryItem] = []
    for n in notes:
        detail = details.get(n.uid)
        if detail is None:
            continue
        prov = provisional[n.uid]
        pnl_result = (
            pnl_calc.compute(detail.events, price_map.get(n.symbol))
            if prov.remaining_qty > 0
            else prov
        )
        out.append(
            NotePnlSummaryItem(
                uid=n.uid, symbol=n.symbol, name=n.name, status=n.status, pnl=pnl_result
            )
        )
    return out
