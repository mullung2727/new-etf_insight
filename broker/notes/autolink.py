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


def _reclassify(uid: str) -> None:
    """노트의 모든 이벤트를 시간순으로 훑어 분류·status를 재계산한다.

    side(매수/매도)는 sync_trades가 출처로 확정해 보존하고, 그 안에서만
    최초매수/추가매수, 분할매도/전량매도를 보유수량 흐름으로 다시 매긴다.
    전체 재계산이라 몇 번을 돌려도 같은 결과(멱등) — 부분체결·추가매수·
    재동기화에 모두 안전. _decide_type+_refresh_status를 대체한다.
    """
    note = store.get_note(uid)
    if note is None:  # 동기화 중 외부 삭제 등
        return
    events = sorted(
        note.events, key=lambda e: (e.executed_at, e.order_no or "", e.id)
    )
    holdings = 0
    saw_sell = False
    for e in events:
        if e.event_type.value in _BUY_TYPES:
            new = EventType.buy.value if holdings == 0 else EventType.add_buy.value
            holdings += e.qty
        else:
            saw_sell = True
            holdings -= e.qty
            new = EventType.sell.value if holdings <= 0 else EventType.partial_sell.value
        if e.event_type.value != new:
            store.update_event_type(e.id, new)
    if holdings <= 0:
        status = NoteStatus.closed
    elif saw_sell:
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

    # 멱등 핵심: 이 order_no가 이미 어느 노트에 속하면(닫힌 노트 포함) 그 노트에
    # 재반영한다. 못 찾을 때만 미청산 노트 재사용 → 없으면 새 노트 생성.
    note_uid = store.find_note_uid_by_order_no(order_no)
    is_update = note_uid is not None
    if note_uid is None:
        active = _active_note(ticker)
        if active is None:
            # 매수면 새 사이클. 매도인데 active 없으면(도입 전 HTS 매수분) 기록만
            # 남긴다 — 사용자 목표는 "전부 기록". 재계산 시 net 음수 → closed.
            active = store.create_note(NoteCreate(symbol=ticker))
            summary["notes"] += 1
        note_uid = active.uid

    # 분류는 _reclassify가 시간순으로 정한다. 여기선 side만 맞는 잠정값을 넣는다.
    provisional = EventType.buy.value if side == "buy" else EventType.sell.value
    store.upsert_event(note_uid, order_no, provisional, price, cntr_qty, executed_at)
    summary["updated" if is_update else "created"] += 1
    _reclassify(note_uid)


def sync_trades(date: str) -> dict[str, int]:
    """``date``(YYYYMMDD) 당일 계좌 전체 체결을 노트에 동기화한다. 멱등.

    매수(sell_tp=2)·매도(sell_tp=1)를 따로 조회해 출처로 side를 확정한다.
    반환: {"created": 신규 이벤트 수, "updated": 갱신된 이벤트 수, "notes": 신규 노트 수}.
    """
    summary = {"created": 0, "updated": 0, "notes": 0}
    # 키움 HTTP 조회는 lock 밖에서(느린 네트워크가 실시간 경로를 막지 않도록).
    buys = orders.get_order_history(date, sell_tp="2")
    sells = orders.get_order_history(date, sell_tp="1")
    with _lock:  # SQLite 쓰기만 직렬화
        for row in buys:
            _reconcile(date, "buy", row, summary)
        for row in sells:
            _reconcile(date, "sell", row, summary)
    logger.info("sync_trades %s -> %s", date, summary)
    return summary
