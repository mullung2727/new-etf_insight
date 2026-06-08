from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter

from kiwoom import quotes

router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get(
    "/{symbol}",
    operation_id="get_quote",
    summary="주식 현재가 조회",
)
def get_quote(symbol: str) -> Any:
    """6자리 종목코드의 현재가를 반환한다. 예: 005930 (삼성전자)"""
    return quotes.get_quote(symbol).model_dump()


@router.get(
    "/{symbol}/orderbook",
    operation_id="get_orderbook",
    summary="주식 호가 조회",
)
def get_orderbook(symbol: str) -> Any:
    """6자리 종목코드의 매수/매도 호가 잔량을 반환한다."""
    return quotes.get_orderbook(symbol)


@router.get(
    "/{symbol}/daily",
    operation_id="get_daily_chart",
    summary="주식 일봉차트 조회",
)
def get_daily_chart(symbol: str, base_dt: str | None = None) -> Any:
    """6자리 종목코드의 일봉 배열을 반환한다. base_dt(YYYYMMDD) 기본값=오늘."""
    base = base_dt or datetime.now().strftime("%Y%m%d")
    return quotes.get_daily_chart(symbol, base)
