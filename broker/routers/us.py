from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from kiwoom import us
from kiwoom.client import KiwoomError
from kiwoom.config import load_config
from kiwoom.guards import (
    check_order_source,
    check_us_profile,
    check_us_strategy_order,
    parse_max_us_order_usd,
)
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


@router.post("/token/rotate", operation_id="rotate_us_token", summary="US 토큰 폐기 후 재발급 (리서치 녹화기 전용)")
def rotate_us_token() -> Any:
    """US 토큰을 폐기하고 재발급해 만료 시각을 미룬다. 새 만료 epoch 반환."""
    # :8001·:8002는 국내 웹소켓이 같은 토큰을 써서 회전 금지 — US 계좌 broker(:8003)에서만.
    if load_config().profile != "US":
        raise HTTPException(status_code=422, detail="US 계좌 broker(KIWOOM_PROFILE=US)에서만 토큰 회전")
    from kiwoom import auth
    try:
        expires_at = auth.rotate_token()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"expires_at": expires_at}


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
    price: float | None = None  # USD. 지정가·LOC 필수, 시장가는 무시(항상 현재가로 상한 계산)
    source: str
    dry_run: bool = False


@router.post(
    "/orders/strategy",
    operation_id="place_us_strategy_order",
    summary="미국 전략 주문 (매수 ust20000/매도 ust20001)",
)
def place_us_strategy_order(req: UsOrderRequest) -> Any:
    """전략 전용 미국 주문. US 계좌 broker에서만 받는다.

    처리 순서: source 허용 → US 프로파일 → 형식(dry_run 전) → dry_run 반환 →
    매수 상한(현재가 기준) → 키움 전송. 검증 실패는 422.
    키움 거부는 HTTP 200 {status: rejected}로 반환한다.
    성공 주문은 kiwoom_trade_history에 기록한다(order_type "US:<trde_tp>").
    MCP에는 노출하지 않는다(main.py exclude_operations).
    """
    # 1. source 허용 검사
    try:
        check_order_source(req.source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 2. 계좌 검사 — US 프로파일 broker에서만 미국 전략 주문
    try:
        check_us_profile(load_config().profile)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 3. 형식 검사 (dry_run 전)
    code = req.code.upper()
    if not _CODE_RE.match(code):
        raise HTTPException(status_code=422, detail="code는 영문·숫자·'.'·'-' 1~10자여야 함")
    if req.side not in ("buy", "sell"):
        raise HTTPException(
            status_code=422, detail=f"side는 buy/sell이어야 함: side={req.side!r}"
        )
    if isinstance(req.qty, bool) or not isinstance(req.qty, int) or req.qty <= 0:
        raise HTTPException(
            status_code=422, detail=f"수량은 1 이상이어야 함: qty={req.qty}"
        )
    if req.trde_tp not in _TRDE_TPS:
        raise HTTPException(
            status_code=422,
            detail=f"trde_tp는 03(시장가)/00(지정가)/30(LOC)이어야 함: {req.trde_tp!r}",
        )
    if req.trde_tp in _PRICE_REQUIRED:
        price = req.price
        if (
            price is None
            or isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(float(price))
            or float(price) <= 0
        ):
            raise HTTPException(
                status_code=422,
                detail=f"trde_tp={req.trde_tp} 주문은 양의 유한 price(USD)가 필요함: price={req.price!r}",
            )
    # 4. dry_run이면 여기서 반환 (현재가 조회·주문 없음)
    if req.dry_run:
        return {"status": "dry_run", "order_no": "",
                "message": f"dry_run: {code} {req.side} {req.qty}주, 키움 전송 없음"}
    # 5-6. 매수만 금액 상한. 매도는 상한 파싱·현재가 조회 없이 주문으로.
    if req.side == "buy":
        try:
            max_usd = parse_max_us_order_usd(os.getenv("MAX_US_ORDER_USD"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            cur = us.get_us_quote(code)["price"]
        except KiwoomError as exc:
            logger.warning("us strategy quote error raw: %s", exc)
            return {"status": "rejected", "order_no": "", "message": str(exc)}
        if (
            cur is None
            or isinstance(cur, bool)
            or not isinstance(cur, (int, float))
            or not math.isfinite(float(cur))
            or float(cur) <= 0
        ):
            raise HTTPException(
                status_code=422,
                detail="시장가 주문 예상금액 확인 실패 — 현재가 조회 불가. 주문을 보내지 않음",
            )
        cur_f = float(cur)
        if req.trde_tp == "03":
            unit = cur_f  # 시장가는 요청 price 무시, 항상 현재가
        else:
            assert req.price is not None  # 위 형식 검사에서 양의 유한 값 확인됨
            unit = max(float(req.price), cur_f)
        try:
            check_us_strategy_order(req.side, req.qty, req.qty * unit, max_usd)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    order_price = req.price if req.trde_tp in _PRICE_REQUIRED else None
    try:
        placed = us.place_us_order(code, req.side, req.qty, req.trde_tp, order_price)
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
                price=order_price if order_price is not None else 0,
                status="submitted",
                source=req.source,
                raw=json.dumps(placed["raw"], ensure_ascii=False),
            )
        except Exception:  # 기록 실패가 주문 응답을 막지 않도록
            logger.exception("미국 거래 원장 기록 실패: order_no=%s", order_no)

    message = str(placed["raw"].get("return_msg", ""))
    return {"status": "submitted", "order_no": order_no,
            "message": message or f"미국 {req.side} 주문 접수: {code} {req.qty}주"}
