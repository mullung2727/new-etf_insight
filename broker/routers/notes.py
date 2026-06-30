from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Response

from notes import autolink, store
from notes.models import (
    EventCreate,
    Note,
    NoteCreate,
    NoteDetail,
    NoteEvent,
    NoteStatus,
    NoteUpdate,
)

router = APIRouter(prefix="/notes", tags=["notes"])

# 한국은 DST가 없으므로 고정 오프셋(notes/trades.py와 동일 패턴).
_KST = timezone(timedelta(hours=9))


def _seoul_today() -> str:
    return datetime.now(_KST).strftime("%Y%m%d")


@router.post(
    "",
    operation_id="create_note",
    summary="투자노트 생성",
    response_model=Note,
)
def create_note(req: NoteCreate) -> Note:
    """새 투자노트(투자 thesis)를 만든다. 종목·목표가·보유기간·매수이유를
    기록한다. status는 open으로 시작. 매수/매도 체결은 add_note_event로 따로
    기록한다."""
    return store.create_note(req)


@router.get(
    "",
    operation_id="list_notes",
    summary="투자노트 목록 조회",
    response_model=list[Note],
)
def list_notes(
    symbol: str | None = None,
    status: NoteStatus | None = None,
    user_id: str | None = "local",
) -> list[Note]:
    """투자노트 목록을 조회한다. symbol·status·user_id로 필터링한다."""
    return store.list_notes(
        symbol=symbol,
        status=status.value if status else None,
        user_id=user_id,
    )


@router.post(
    "/sync-trades",
    operation_id="sync_trades",
    summary="키움 체결내역을 투자노트에 동기화 (kt00007)",
)
def sync_trades(date: str | None = None) -> dict[str, int]:
    """당일(또는 지정일) 계좌 전체 체결을 투자노트에 자동 반영한다.

    date 생략 시 오늘(KST). kt00007을 권위 소스로 매수/매도 체결을 order_no
    멱등 키로 upsert하고 노트 status를 재계산한다. 반환은
    {"created", "updated", "notes"} 요약. 시간당 폴링·실시간 체결통보가 호출한다.
    """
    return autolink.sync_trades(date or _seoul_today())


@router.get(
    "/{uid}",
    operation_id="get_note",
    summary="투자노트 단건 조회(이벤트 포함)",
    response_model=NoteDetail,
)
def get_note(uid: str) -> NoteDetail:
    """투자노트 한 건을 매수/매도 이벤트와 함께 반환한다."""
    note = store.get_note(uid)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return note


@router.patch(
    "/{uid}",
    operation_id="update_note",
    summary="투자노트 수정",
    response_model=NoteDetail,
)
def update_note(uid: str, req: NoteUpdate) -> NoteDetail:
    """투자노트를 부분 수정한다. status(open/partial/closed)·목표가·보유기간·
    매수이유·메모를 갱신한다. None인 필드는 그대로 둔다."""
    note = store.update_note(uid, req)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    return note


@router.delete(
    "/{uid}",
    operation_id="delete_note",
    summary="투자노트 삭제",
)
def delete_note(uid: str) -> Response:
    """투자노트를 삭제한다. 연결된 이벤트도 함께 삭제된다(CASCADE)."""
    if not store.delete_note(uid):
        raise HTTPException(status_code=404, detail="note not found")
    return Response(status_code=204)


@router.post(
    "/{uid}/events",
    operation_id="add_note_event",
    summary="투자노트에 매수/매도 이벤트 기록",
    response_model=NoteEvent,
)
def add_note_event(uid: str, req: EventCreate) -> NoteEvent:
    """투자노트에 체결 이벤트(buy/add_buy/partial_sell/sell)를 기록한다.
    가격·수량·체결시각은 호출 측(LLM)이 잔고/시세 툴로 조회해 채운다."""
    event = store.add_event(uid, req)
    if event is None:
        raise HTTPException(status_code=404, detail="note not found")
    return event


@router.delete(
    "/{uid}/events/{event_id}",
    operation_id="delete_note_event",
    summary="투자노트 이벤트 삭제",
)
def delete_note_event(uid: str, event_id: int) -> Response:
    """투자노트의 특정 이벤트를 삭제한다."""
    if not store.delete_event(uid, event_id):
        raise HTTPException(status_code=404, detail="event not found")
    return Response(status_code=204)
