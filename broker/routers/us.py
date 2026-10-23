from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from kiwoom import us
from kiwoom.client import KiwoomError
from kiwoom.guards import check_order_source, check_us_strategy_order
from notes import trades

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/us", tags=["us"])

_CODE_RE = re.compile(r"^[A-Z0-9.-]{1,10}$")

# 뉴욕 정규장 16:00 마감 + 종가 확정 여유. 이 시각 이후면 오늘 봉도 끝난 날로 본다.
SESSION_DONE = time(16, 30)


def _cutoff(now_ny: datetime) -> str:
    day = now_ny.date()
    if now_ny.time() >= SESSION_DONE:
        day += timedelta(days=1)
    return day.strftime("%Y%m%d")


@router.get("/daily", operation_id="get_us_daily", summary="미국 종목 일봉 조회 (키움 usa06012)")
def get_us_daily(
    code: str, from_: str = Query(..., alias="from"), today_ny: str | None = None
) -> Any:
    """미국 종목 일봉을 날짜 오름차순으로 반환한다. 예: ?code=QQQ&from=20261013.

    끝난 날만 반환(뉴욕 기준 당일 진행 봉 제외), 수정주가 적용, USD.
    today_ny(YYYYMMDD): 이 날짜 이상 봉은 버림. 생략 시 기본값: 뉴욕 16:30 이후면 내일 → 오늘 봉 포함.
    """
    if len(from_) != 8 or not from_.isdigit():
        raise HTTPException(status_code=422, detail="from은 YYYYMMDD 8자리 숫자여야 함")
    upper = code.upper()
    if not _CODE_RE.match(upper):
        raise HTTPException(status_code=422, detail="code는 영문·숫자·'.'·'-' 1~10자여야 함")
    today = today_ny or _cutoff(datetime.now(ZoneInfo("America/New_York")))
    try:
        return us.get_us_daily(upper, from_, today)
    except KiwoomError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/quote", operation_id="get_us_quote", summary="미국 종목 현재가 (키움 usa20101)")
def get_us_quote(code: str) -> Any:
    """현재가·매수/매도 1호가·거래량을 USD로 반환한다. 예: ?code=QQQ."""
    upper = code.upper()
    if not _CODE_RE.match(upper):
        raise HTTPException(status_code=422, detail="code는 영문·숫자·'.'·'-' 1~10자여야 함")
    try:
        return us.get_us_quote(upper)
    except KiwoomError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/balance", operation_id="get_us_balance", summary="미국 원장잔고 (키움 ust21070)")
def get_us_balance() -> Any:
    """전 거래소 보유 잔고를 합쳐 USD로 반환한다. 수량 0 종목 제외."""
    try:
        return us.get_us_balance()
    except KiwoomError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/deposit", operation_id="get_us_deposit", summary="달러 예수금 (키움 ust21110)")
def get_us_deposit() -> Any:
    """USD 예수금·주문가능·출금가능 금액을 반환한다."""
    try:
        return us.get_us_deposit()
    except KiwoomError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/fills", operation_id="get_us_fills", summary="미국 당일 주문체결 (키움 ust21510)")
def get_us_fills() -> Any:
    """당일 주문·체결 목록을 반환한다."""
    try:
        return us.get_us_fills()
    except KiwoomError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


_TRDE_TPS = {"03", "00", "30"}
_PRICE_REQUIRED = {"00", "30"}  # 지정가·LOC는 price 필수


class UsOrderRequest(BaseModel):
    code: str
    side: str  # buy/sell — 형식은 가드가 검증
    qty: int
    trde_tp: str = "03"  # 03 시장가, 00 지정가, 30 LOC
    price: float | None = None  # USD. 지정가·LOC 필수, 시장가는 상한 계산용(없으면 현재가)
    source: str
    dry_run: bool = False


@router.post(
    "/orders/strategy",
    operation_id="place_us_strategy_order",
    summary="미국 전략 주문 (매수 ust20000/매도 ust20001)",
)
def place_us_strategy_order(req: UsOrderRequest) -> Any:
    """전략 전용 미국 주문. 국내 전략 주문과 같은 가드(source 허용·매수 금액 상한)를 지난다.

    검증 실패는 422. 키움 거부는 HTTP 200 {status: rejected}로 반환한다.
    성공 주문은 kiwoom_trade_history에 기록한다(order_type "US:<trde_tp>").
    MCP에는 노출하지 않는다(main.py exclude_operations).
    """
    try:
        check_order_source(req.source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if req.trde_tp not in _TRDE_TPS:
        raise HTTPException(
            status_code=422,
            detail=f"trde_tp는 03(시장가)/00(지정가)/30(LOC)이어야 함: {req.trde_tp!r}",
        )
    if req.trde_tp in _PRICE_REQUIRED and req.price is None:
        raise HTTPException(
            status_code=422, detail=f"trde_tp={req.trde_tp} 주문은 price(USD)가 필요함"
        )
    code = req.code.upper()
    if req.dry_run:
        return {"status": "dry_run", "order_no": "",
                "message": f"dry_run: {code} {req.side} {req.qty}주, 키움 전송 없음"}
    unit = req.price
    if unit is None:
        try:
            unit = us.get_us_quote(code)["price"]
        except KiwoomError as exc:
            logger.warning("us strategy quote error raw: %s", exc)
            return {"status": "rejected", "order_no": "", "message": str(exc)}
    if unit is None:
        raise HTTPException(
            status_code=422,
            detail="시장가 주문 예상금액 확인 실패 — 현재가 조회 불가. 주문을 보내지 않음",
        )
    try:
        max_usd = float(os.getenv("MAX_US_ORDER_USD", "5000"))
    except ValueError:
        raise HTTPException(
            status_code=422, detail="MAX_US_ORDER_USD 파싱 실패 — 숫자여야 함") from None
    try:
        check_us_strategy_order(req.side, req.qty, req.qty * unit, max_usd)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        placed = us.place_us_order(code, req.side, req.qty, req.trde_tp, req.price)
    except KiwoomError as exc:
        logger.warning("us order error raw: %s", exc)
        return {"status": "rejected", "order_no": "", "message": str(exc)}

    order_no = placed["order_no"]
    if order_no:
        try:
            trades.record_trade(
                order_no=order_no,
                ticker=code,
                side=req.side,
                order_type=f"US:{req.trde_tp}",
                qty=req.qty,
                price=int(round(req.price)) if req.price else 0,
                status="submitted",
                source=req.source,
                raw=json.dumps(placed["raw"], ensure_ascii=False),
            )
        except Exception:  # 기록 실패가 주문 응답을 막지 않도록
            logger.exception("미국 거래 원장 기록 실패: order_no=%s", order_no)

    message = str(placed["raw"].get("return_msg", ""))
    return {"status": "submitted", "order_no": order_no,
            "message": message or f"미국 {req.side} 주문 접수: {code} {req.qty}주"}
