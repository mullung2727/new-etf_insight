from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter

from kiwoom import quotes

router = APIRouter(prefix="/quotes", tags=["quotes"])

# 다중 소비자(청산워커 + broker-web 표시)가 3초 폴링해도 상류 ka10095 콜을
# 합치기 위한 짧은 TTL 캐시. broker는 단일 워커(uvicorn --reload)라 모듈 전역으로 충분.
_CACHE: dict[str, tuple[float, list[Any]]] = {}
_CACHE_TTL = 2.0


def _clear_cache() -> None:  # 테스트용
    _CACHE.clear()


@router.get(
    "",
    operation_id="get_watchlist_quotes",
    summary="복수종목 일괄시세 조회",
)
def get_watchlist_quotes(codes: str) -> Any:
    """쉼표구분 종목코드들의 일괄시세를 반환한다. 예: ?codes=005930,000660

    2초 TTL 캐시 — 동일 코드셋 반복 폴링 시 상류 ka10095 콜을 합친다.
    """
    code_list = [c for c in codes.split(",") if c.strip()]
    key = ",".join(sorted(code_list))
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    data = quotes.get_watchlist_quotes(code_list)
    _CACHE[key] = (now, data)
    return data


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
