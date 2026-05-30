from __future__ import annotations

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
