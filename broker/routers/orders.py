from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

from typing import Any

from fastapi import APIRouter, HTTPException

from kiwoom import orders
from kiwoom.client import KiwoomError
from kiwoom.config import get_current_env
from kiwoom.guards import OrderRejected
from kiwoom.models import OrderRequest, OrderResult
from notes import trades

router = APIRouter(prefix="/orders", tags=["orders"])

_AFTER_HOURS_PATTERNS = re.compile(r"장 ?종료|시간외|업무시간|시장.*종료|개장.*전")
_TICKER_PREFIX = re.compile(r"^\D+")  # stk_cd "A069500" → "069500"


def _to_int(value: Any) -> int:
    """0-padding 부호 포함 문자열("0000012345")을 int로. 빈값/오류는 0."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


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
    """주문을 실행한다. 금액/수량 상한 가드 초과 시 422로 거부된다.

    성공(accepted=True + order_no) 시 거래 원장(kiwoom_trade_history)에 기록한다.
    거부/실패는 order_no가 없어 기록하지 않는다(호출자가 422로 인지).
    """
    try:
        result = orders.place_order(req)
    except OrderRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KiwoomError as exc:
        logger.warning("order error raw: %s", exc)
        raise HTTPException(status_code=422, detail=_friendly_order_error(exc)) from exc

    if result.accepted and result.order_no:
        try:
            trades.record_trade(
                order_no=result.order_no,
                ticker=req.symbol,
                side=req.side.value,
                order_type=req.order_type.value,
                qty=req.qty,
                price=req.price,
                status="submitted",
                source=req.source,
                raw=json.dumps(result.raw, ensure_ascii=False),
            )
        except Exception:  # 기록 실패가 주문 응답을 막지 않도록
            logger.exception("거래 원장 기록 실패: order_no=%s", result.order_no)

    return result


@router.get(
    "/history",
    operation_id="get_order_history",
    summary="당일 체결내역 조회 (kt00007)",
)
def get_order_history(date: str) -> list[dict[str, Any]]:
    """당일 매수 체결내역을 정규화해 반환한다 (체결 대조 배치용).

    date: 주문일자 YYYYMMDD. 각 항목은 0-padding을 제거한 정수/순수 종목코드.
    """
    rows = orders.get_order_history(date)
    return [
        {
            "order_no": str(item.get("ord_no", "") or ""),
            "ticker": _TICKER_PREFIX.sub("", str(item.get("stk_cd", "") or "")),
            "cntr_qty": _to_int(item.get("cntr_qty")),
            "cntr_uv": _to_int(item.get("cntr_uv")),
            "ord_remnq": _to_int(item.get("ord_remnq")),
            "raw": item,
        }
        for item in rows
    ]


@router.delete(
    "/{order_no}",
    operation_id="cancel_order",
    summary="미체결 주문 취소",
    response_model=OrderResult,
)
def cancel_order(order_no: str, symbol: str, qty: int = 0) -> OrderResult:
    """미체결 주문을 취소한다. qty=0이면 잔량 전부 취소."""
    return orders.cancel_order(order_no, symbol, qty)
