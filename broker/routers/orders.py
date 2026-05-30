from __future__ import annotations

from fastapi import APIRouter, HTTPException

from kiwoom import orders
from kiwoom.guards import OrderRejected
from kiwoom.models import OrderRequest, OrderResult

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post(
    "",
    operation_id="place_order",
    summary="매수/매도 주문",
    response_model=OrderResult,
)
def place_order(req: OrderRequest) -> OrderResult:
    """주문을 실행한다. 금액/수량 상한 가드 초과 시 422로 거부된다."""
    try:
        return orders.place_order(req)
    except OrderRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    "/{order_no}",
    operation_id="cancel_order",
    summary="미체결 주문 취소",
    response_model=OrderResult,
)
def cancel_order(order_no: str, symbol: str, qty: int = 0) -> OrderResult:
    """미체결 주문을 취소한다. qty=0이면 잔량 전부 취소."""
    return orders.cancel_order(order_no, symbol, qty)
