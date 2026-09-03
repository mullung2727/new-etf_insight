from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

logger = logging.getLogger(__name__)

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kiwoom import orders
from kiwoom.client import KiwoomError
from kiwoom.config import get_current_env
from kiwoom.guards import OrderRejected
from kiwoom.models import OrderRequest, OrderResult
from notes import trades

router = APIRouter(prefix="/orders", tags=["orders"])

_AFTER_HOURS_PATTERNS = re.compile(r"장 ?종료|시간외|업무시간|시장.*종료|개장.*전")
_TICKER_PREFIX = re.compile(r"^\D+")  # stk_cd "A069500" → "069500"
# 키움은 사람 메시지를 "[코드](서브코드:메시지)"로 감싼다 — 서브코드와 텍스트를 뽑는다.
_KIWOOM_MSG = re.compile(r"\(([A-Z0-9]+):([^)]+)\)")

# 관측된 주문거절 서브코드 → 사용자 행동지침. 원문 메시지는 그대로 두고 hint만 덧붙인다.
# 키움 문서에 서브코드 테이블이 없어 미리 채우지 않고, 실제 만난 코드만 자기학습식으로 추가.
_ORDER_HINT = {
    "800033": "당일 매수분(T+2 미결제)이거나 기존 매도주문이 수량을 잡고 있는지 확인",
}


def _to_int(value: Any) -> int:
    """0-padding 부호 포함 문자열("0000012345")을 int로. 빈값/오류는 0."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    """부호 포함 백분율 문자열("+5.00")을 float로. 빈값/오류는 0.0."""
    try:
        return float(str(value).strip().lstrip("+"))
    except (TypeError, ValueError):
        return 0.0


def _to_decimal(value: Any) -> Decimal:
    """키움의 소수 문자열 금액을 정밀하게 변환한다. 빈값/오류는 0."""
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _friendly_order_error(exc: KiwoomError) -> str:
    msg = str(exc)
    # 키움 원문에서 서브코드와 사람 메시지를 뽑는다("800033", "매도가능수량이 부족..." 등).
    inner = _KIWOOM_MSG.search(msg)
    code = inner.group(1) if inner else ""
    text = inner.group(2).strip() if inner else msg
    # '모의투자 장종료'류(장 시간 밖)만 장중 안내로 바꾼다. "모의투자" 글자만으로
    # 매도가능수량 부족 등 다른 원인을 장중 안내로 덮어쓰지 않는다.
    if get_current_env() == "paper" and "모의투자" in text and _AFTER_HOURS_PATTERNS.search(text):
        return "모의투자는 장 중(09:00~15:30)에만 가능합니다"
    hint = _ORDER_HINT.get(code)
    if hint:
        return f"{text} ({hint})"
    if _AFTER_HOURS_PATTERNS.search(text):
        return f"장 시간 외 주문 불가: {text}"
    return text or msg


def _submit_order(req: OrderRequest, *, enforce_amount_cap: bool) -> OrderResult:
    """공통 주문·원장 기록. 금액상한 정책은 호출한 서버 라우트가 결정한다."""
    try:
        result = orders.place_order(req, enforce_amount_cap=enforce_amount_cap)
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


@router.post(
    "",
    operation_id="place_order",
    summary="수동 매수/매도 주문",
    response_model=OrderResult,
)
def place_order(req: OrderRequest) -> OrderResult:
    """웹/MCP 수동 주문을 실행한다. 전략용 금액 상한은 적용하지 않는다.

    수량·지정가 형식 검증과 키움 자체 주문 검증은 그대로 유지한다.
    성공 주문은 kiwoom_trade_history에 source와 함께 기록한다.
    """
    return _submit_order(req, enforce_amount_cap=False)


@router.post(
    "/strategy",
    include_in_schema=False,
    response_model=OrderResult,
)
def place_strategy_order(req: OrderRequest) -> OrderResult:
    """종가베팅·눌림목 전용 내부 주문. 전략용 금액 상한을 항상 적용한다."""
    return _submit_order(req, enforce_amount_cap=True)


@router.get(
    "/history",
    operation_id="get_order_history",
    summary="당일 체결내역 조회 (kt00007)",
)
def get_order_history(date: str, side: str = "buy") -> list[dict[str, Any]]:
    """당일 체결내역을 정규화해 반환한다 (체결 대조·매도 청산 확인용).

    date: 주문일자 YYYYMMDD. side: buy(기본·매수 체결대조)/sell(매도 청산 확인).
    각 항목은 0-padding을 제거한 정수/순수 종목코드.
    """
    sell_tp = "1" if side == "sell" else "2"
    rows = orders.get_order_history(date, sell_tp=sell_tp)
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


@router.get(
    "/unfilled",
    operation_id="get_unfilled",
    summary="미체결 주문 조회 (ka10075)",
)
def get_unfilled(side: str = "sell") -> list[dict[str, Any]]:
    """미체결 주문을 정규화해 반환한다 (매도 청산 체결확인·재주문 가드용).

    side: sell(기본)/buy/all. 우리 주문이 목록서 사라지면 체결 완료로 판단.
    """
    rows = orders.get_unfilled(side)
    return [
        {
            "order_no": str(item.get("ord_no", "") or ""),
            "ticker": _TICKER_PREFIX.sub("", str(item.get("stk_cd", "") or "")),
            "stk_nm": str(item.get("stk_nm", "") or ""),
            "ord_qty": _to_int(item.get("ord_qty")),
            "ord_price": _to_int(item.get("ord_pric")),
            "oso_qty": _to_int(item.get("oso_qty")),
            "ord_stt": str(item.get("ord_stt", "") or ""),
            "io_tp_nm": str(item.get("io_tp_nm", "") or ""),
            "tm": str(item.get("tm", "") or ""),
            "raw": item,
        }
        for item in rows
    ]


@router.get(
    "/realized/{ticker}",
    operation_id="get_today_realized",
    summary="종목 실현손익 net (ka10077 당일 / date 지정 시 ka10072)",
)
def get_today_realized(ticker: str, date: str | None = None) -> dict[str, Any]:
    """해당 종목의 net 실현손익(수수료·세금 차감)을 반환한다.

    date 없으면 당일(ka10077), date=YYYYMMDD 지정 시 그 날(ka10072)로 과거 소급.
    매도 청산 직후 호출해 gross 추정 대신 키움 권위값을 저장하는 용도.
    같은 날 같은 종목 매도가 여러 건이면 수수료·세금·손익금을 합산하고
    손익율은 원가기준 재계산한다(close-bet은 보통 1건이라 단일 행).
    """
    rows = orders.get_realized_by_date(ticker, date) if date else orders.get_today_realized(ticker)
    cmsn = sum(_to_int(r.get("tdy_trde_cmsn")) for r in rows)
    tax = sum(_to_int(r.get("tdy_trde_tax")) for r in rows)
    sel_pl_decimal = sum((_to_decimal(r.get("tdy_sel_pl")) for r in rows), Decimal(0))
    sel_pl = int(sel_pl_decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    qty = sum(_to_int(r.get("cntr_qty")) for r in rows)
    cost = sum(
        (_to_decimal(r.get("buy_uv")) * _to_int(r.get("cntr_qty")) for r in rows),
        Decimal(0),
    )
    if len(rows) == 1:
        pnl_pct = _to_float(rows[0].get("pl_rt"))
    else:
        pnl_pct = float(round(sel_pl_decimal / cost * 100, 2)) if cost else 0.0
    return {
        "ticker": _TICKER_PREFIX.sub("", ticker),
        "found": bool(rows),
        "pnl_pct": pnl_pct,
        "sel_pl_won": sel_pl,
        "cmsn": cmsn,
        "tax": tax,
        "qty": qty,
    }


@router.delete(
    "/{order_no}",
    operation_id="cancel_order",
    summary="미체결 주문 취소",
    response_model=OrderResult,
)
def cancel_order(order_no: str, symbol: str, qty: int = 0) -> OrderResult:
    """미체결 주문을 취소한다. qty=0이면 잔량 전부 취소."""
    return orders.cancel_order(order_no, symbol, qty)


class ModifyRequest(BaseModel):
    symbol: str
    price: int
    qty: int = 0  # 0=잔량 전부


@router.patch(
    "/{order_no}",
    operation_id="modify_order",
    summary="미체결 주문 정정 (kt10002)",
    response_model=OrderResult,
)
def modify_order(order_no: str, req: ModifyRequest) -> OrderResult:
    """미체결 주문 가격을 정정한다. qty=0이면 잔량 전부."""
    try:
        return orders.modify_order(order_no, req.symbol, req.price, req.qty)
    except KiwoomError as exc:
        logger.warning("modify error raw: %s", exc)
        raise HTTPException(status_code=422, detail=_friendly_order_error(exc)) from exc
