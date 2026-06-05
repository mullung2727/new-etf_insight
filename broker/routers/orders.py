from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException

from kiwoom import orders
from kiwoom.client import KiwoomError
from kiwoom.config import get_current_env
from kiwoom.guards import OrderRejected
from kiwoom.models import OrderRequest, OrderResult

router = APIRouter(prefix="/orders", tags=["orders"])

_AFTER_HOURS_PATTERNS = re.compile(r"장 ?종료|시간외|업무시간|시장.*종료|개장.*전")


def _friendly_order_error(exc: KiwoomError) -> str:
    msg = str(exc)
    if get_current_env() == "paper" and "모의투자" in msg:
        return "모의투자는 장 중(09:00~15:30)에만 가능합니다"
    if _AFTER_HOURS_PATTERNS.search(msg):
        return f"장 시간 외 주문 불가: {msg}"
    # Strip the leading "TR_ID HTTP xxx: " or "TR_ID return_code=N: " prefix if present
    clean = re.sub(r"^[A-Z0-9_]+ (?:HTTP \d+|return_code=\S+): ", "", msg)
    return clean or msg


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
    except KiwoomError as exc:
        logger.warning("order error raw: %s", exc)
        raise HTTPException(status_code=422, detail=_friendly_order_error(exc)) from exc


@router.delete(
    "/{order_no}",
    operation_id="cancel_order",
    summary="미체결 주문 취소",
    response_model=OrderResult,
)
def cancel_order(order_no: str, symbol: str, qty: int = 0) -> OrderResult:
    """미체결 주문을 취소한다. qty=0이면 잔량 전부 취소."""
    return orders.cancel_order(order_no, symbol, qty)
