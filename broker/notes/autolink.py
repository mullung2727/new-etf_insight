"""키움 체결 → 투자노트 자동 연결 (reconciler).

kt00007(당일 체결내역, ``kiwoom.orders.get_order_history``)을 단일 권위 소스로
삼아 계좌 전체 체결(broker API + HTS 수동매매)을 ``notes``/``note_events``에
반영한다. 멱등 키는 주문번호(``ord_no``) — kt00007이 주문번호 단위 누적
집계라 부분→전량 체결 시 같은 order_no의 qty가 커지므로 upsert한다.

쓰기 경로는 ``sync_trades`` 하나뿐. 실시간 WS 체결통보와 시간당 폴링 둘 다
이 함수만 호출하며, 실시간은 증분을 직접 쓰지 않고 "재동기화 트리거" 역할만
한다(kt00007 집계와 항상 일관). ``_lock``으로 동시 쓰기를 직렬화한다.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from kiwoom import orders

from . import store
from .models import EventType, NoteCreate, NoteStatus, NoteUpdate

logger = logging.getLogger(__name__)

# 폴링(FastAPI 스레드풀) + 실시간(asyncio.to_thread) 동시 호출을 직렬화.
# 단일 SQLite 연결을 공유하므로 동시 쓰기 충돌(database is locked)을 예방한다.
_lock = threading.Lock()

# routers/orders.py 와 동일 로직(레이어 의존 회피 위해 인라인 복제).
_TICKER_PREFIX = re.compile(r"^\D+")  # stk_cd "A005930" → "005930"

_BUY_TYPES = (EventType.buy.value, EventType.add_buy.value)
_SELL_TYPES = (EventType.partial_sell.value, EventType.sell.value)


def _to_int(value: Any) -> int:
    """0-padding 부호 포함 문자열("0000012345")을 int로. 빈값/오류는 0."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _executed_at(date: str, ord_tm: str) -> str:
    """YYYYMMDD + HHMMSS → "YYYY-MM-DD HH:MM:SS"(표시용). 형식 불명 시 원본 결합."""
    d = date.strip()
    t = (ord_tm or "").strip()
    if len(d) == 8 and d.isdigit():
        d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    if len(t) == 6 and t.isdigit():
        t = f"{t[:2]}:{t[2:4]}:{t[4:]}"
    return f"{d} {t}".strip()


def _active_note(ticker: str):
    """해당 종목의 미청산(open/partial) 최근 노트. 없으면 None."""
    notes = store.list_notes(symbol=ticker)
    for note in notes:  # list_notes는 created_at DESC 정렬
        if note.status in (NoteStatus.open, NoteStatus.partial):
            return note
    return None


def _decide_type(events, side: str, order_no: str, cntr_qty: int) -> str:
    """이 체결의 event_type을 결정한다. 재동기화해도 동일 결과(멱등).

    events: 노트의 기존 이벤트(NoteEvent 리스트). 현재 order_no 행은 새 qty로
    대체해 계산한다.
    """
    buy = sum(
        e.qty for e in events
        if e.event_type.value in _BUY_TYPES and e.order_no != order_no
    )
    sell = sum(
        e.qty for e in events
        if e.event_type.value in _SELL_TYPES and e.order_no != order_no
    )
    if side == "buy":
        return EventType.buy.value if buy == 0 else EventType.add_buy.value
    # sell: 이 매도까지 반영한 net이 0 이하면 전량매도, 아니면 분할매도
    net = buy - (sell + cntr_qty)
    return EventType.sell.value if net <= 0 else EventType.partial_sell.value


def _refresh_status(uid: str) -> None:
    """이벤트 합으로 노트 status를 재계산한다(보유수량 아님 — 이벤트 net 기준)."""
    note = store.get_note(uid)
    if note is None:
        return
    buy = sum(e.qty for e in note.events if e.event_type.value in _BUY_TYPES)
    sell = sum(e.qty for e in note.events if e.event_type.value in _SELL_TYPES)
    net = buy - sell
    if net <= 0:
        status = NoteStatus.closed
    elif sell > 0:
        status = NoteStatus.partial
    else:
        status = NoteStatus.open
    if note.status != status:
        store.update_note(uid, NoteUpdate(status=status))


def _reconcile(date: str, side: str, row: dict[str, Any], summary: dict[str, int]) -> None:
    cntr_qty = _to_int(row.get("cntr_qty"))
    if cntr_qty <= 0:
        return  # 미체결/취소 행 skip
    order_no = str(row.get("ord_no", "") or "").strip()
    if not order_no:
        return
    ticker = _TICKER_PREFIX.sub("", str(row.get("stk_cd", "") or ""))
    if not ticker:
        return
    price = _to_int(row.get("cntr_uv"))
    executed_at = _executed_at(date, str(row.get("ord_tm", "") or ""))

    note = _active_note(ticker)
    if note is None:
        # 매수면 새 사이클 시작. 매도인데 active 노트 없으면(도입 전 HTS 매수분)
        # 기록은 남긴다 — 사용자 목표는 "전부 기록". net 음수 → closed 처리됨.
        note = store.create_note(NoteCreate(symbol=ticker))
        summary["notes"] += 1
        existing = []
    else:
        existing = store.get_note(note.uid).events

    already = any(e.order_no == order_no for e in existing)
    etype = _decide_type(existing, side, order_no, cntr_qty)
    store.upsert_event(note.uid, order_no, etype, price, cntr_qty, executed_at)
    summary["updated" if already else "created"] += 1
    _refresh_status(note.uid)


def sync_trades(date: str) -> dict[str, int]:
    """``date``(YYYYMMDD) 당일 계좌 전체 체결을 노트에 동기화한다. 멱등.

    매수(sell_tp=2)·매도(sell_tp=1)를 따로 조회해 출처로 side를 확정한다.
    반환: {"created": 신규 이벤트 수, "updated": 갱신된 이벤트 수, "notes": 신규 노트 수}.
    """
    summary = {"created": 0, "updated": 0, "notes": 0}
    with _lock:
        buys = orders.get_order_history(date, sell_tp="2")
        sells = orders.get_order_history(date, sell_tp="1")
        for row in buys:
            _reconcile(date, "buy", row, summary)
        for row in sells:
            _reconcile(date, "sell", row, summary)
    logger.info("sync_trades %s -> %s", date, summary)
    return summary
