"""호가 수집기용 0D 실시간 구독 제어 (SPEC_ORDERBOOK_SNAPSHOT_RECORDER §5).

종목 입력은 접두사 없는 6자리 영숫자 코드. broker 가 KRX: 를 붙여 group 2 로 등록한다.
POST 는 추가(멱등), DELETE 는 group 2 의 0D 전부 해제. 체결통보 00(group 1)은 건드리지 않는다.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from kiwoom.ws.manager import ControlError, ws_manager

router = APIRouter(prefix="/realtime", tags=["realtime"])

Code = Annotated[str, StringConstraints(pattern=r"^[0-9A-Z]{6}$")]


class OrderbookCodes(BaseModel):
    codes: list[Code] = Field(min_length=1)


@router.get("/orderbook", operation_id="get_realtime_orderbook", summary="0D 호가 구독 상태")
def get_orderbook() -> dict:
    return ws_manager.orderbook_status()


@router.post("/orderbook", operation_id="add_realtime_orderbook", summary="0D 호가 구독 추가")
async def add_orderbook(req: OrderbookCodes) -> dict:
    try:
        return await ws_manager.add_orderbook(req.codes)
    except ControlError as exc:
        raise HTTPException(exc.status, exc.detail) from None


@router.delete("/orderbook", operation_id="remove_realtime_orderbook", summary="0D 호가 구독 전부 해제")
async def remove_orderbook() -> dict:
    try:
        return await ws_manager.remove_orderbook()
    except ControlError as exc:
        raise HTTPException(exc.status, exc.detail) from None
