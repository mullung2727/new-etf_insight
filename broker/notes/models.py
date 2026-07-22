"""투자노트 입출력 모델 — MCP 툴 계약.

``kiwoom/models.py`` 스타일(``_Base``, Enum)을 미러한다. Create/Update는
LLM이 채워 넣는 입력 계약, Note/NoteEvent/NoteDetail은 응답 계약.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class NoteStatus(str, Enum):
    idea = "idea"        # 매수 전 조사 단계, 체결 이벤트 없음
    open = "open"        # 보유 중, 미청산
    partial = "partial"  # 일부 매도
    closed = "closed"    # 전량 매도


class EventType(str, Enum):
    buy = "buy"                   # 최초 매수
    add_buy = "add_buy"           # 추가 매수
    partial_sell = "partial_sell" # 분할 매도
    sell = "sell"                 # 전량 매도


class NoteCreate(_Base):
    symbol: str = Field(description="종목코드 6자리, 예: 005930")
    target_price: int | None = Field(default=None, description="목표가(원)")
    entry_price: int | None = Field(
        default=None, description="매수 진입 희망가(원). 도달하면 알림"
    )
    holding_period: str | None = Field(
        default=None, description="예상 보유기간(자유텍스트), 예: '3개월'"
    )
    buy_reason: str | None = Field(default=None, description="매수 이유")
    memo: str | None = Field(default=None, description="자유 메모")
    user_id: str = Field(default="local", description="사용자 식별자(기본 local)")


class NoteUpdate(_Base):
    """부분 수정. None 필드는 변경하지 않는다."""

    status: NoteStatus | None = None
    target_price: int | None = None
    entry_price: int | None = None
    alert_off: int | None = Field(
        default=None, ge=0, le=1, description="1이면 진입가 도달 알림을 끈다"
    )
    holding_period: str | None = None
    buy_reason: str | None = None
    memo: str | None = None


class Note(_Base):
    uid: str
    symbol: str
    name: str | None = None
    status: NoteStatus
    target_price: int | None = None
    entry_price: int | None = None
    alert_off: int = 0
    alerted_on: str | None = None
    holding_period: str | None = None
    buy_reason: str | None = None
    memo: str | None = None
    user_id: str
    created_at: str
    updated_at: str


class EventCreate(_Base):
    event_type: EventType = Field(description="buy/add_buy/partial_sell/sell")
    price: int = Field(ge=0, description="체결가(원)")
    qty: int = Field(gt=0, description="수량(주)")
    executed_at: str = Field(description="체결 시각 ISO8601, 예: 2026-05-30")
    memo: str | None = Field(default=None, description="이벤트 메모")


class NoteEvent(_Base):
    id: int
    note_uid: str
    event_type: EventType
    price: int
    qty: int
    executed_at: str
    memo: str | None = None
    order_no: str | None = None
    created_at: str


class NotePnl(_Base):
    remaining_qty: int
    avg_cost: float | None = None  # 남은 보유분 평단가(주당)
    invested_amt: int              # 총 매수원가 + 매수수수료(sunk)
    recovered_amt: int             # 실현매도금액(수수료·세금 차감) + 평가금액(청산가정 차감)
    net_pnl: int                   # recovered_amt - invested_amt
    net_pnl_pct: float | None = None
    fee_applied: bool              # 수수료율이 하나라도 설정돼 있으면 True
    needs_price: bool              # 남은 보유분 있어 현재가 필요한데 못 받았으면 True


class NoteDetail(Note):
    events: list[NoteEvent] = Field(default_factory=list)
    pnl: NotePnl | None = None


class NotePnlSummaryItem(_Base):
    uid: str
    symbol: str
    name: str | None = None
    status: NoteStatus
    pnl: NotePnl
