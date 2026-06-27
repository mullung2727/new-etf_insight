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


def _to_float(value: Any) -> float:
    """부호 포함 백분율 문자열("+5.00")을 float로. 빈값/오류는 0.0."""
    try:
        return float(str(value).strip().lstrip("+"))
    except (TypeError, ValueError):
        return 0.0


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
            "oso_qty": _to_int(item.get("oso_qty")),
            "ord_stt": str(item.get("ord_stt", "") or ""),
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
    sel_pl = sum(_to_int(r.get("tdy_sel_pl")) for r in rows)
    qty = sum(_to_int(r.get("cntr_qty")) for r in rows)
    cost = sum(_to_int(r.get("buy_uv")) * _to_int(r.get("cntr_qty")) for r in rows)
    if len(rows) == 1:
        pnl_pct = _to_float(rows[0].get("pl_rt"))
    else:
        pnl_pct = round(sel_pl / cost * 100, 2) if cost else 0.0
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
