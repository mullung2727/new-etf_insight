"""시세조회 — current price and order book."""

from __future__ import annotations

from typing import Any

from . import tr
from .client import request
from .models import Quote


def _to_int(val: Any) -> int | None:
    """Kiwoom prices come as strings, sometimes signed ('+1200', '-300')."""
    if val is None:
        return None
    try:
        return int(str(val).replace("+", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


# Common keys Kiwoom uses for "current price" across TRs; first hit wins.
_PRICE_KEYS = ("cur_prc", "stk_prpr", "prpr", "last_prc")


def get_quote(symbol: str) -> Quote:
    """Return current price for a 6-digit symbol code (예: '005930')."""
    res = request(tr.TR_STOCK_INFO, tr.EP_STKINFO, {"stk_cd": symbol})
    data = res.data
    price = next((_to_int(data[k]) for k in _PRICE_KEYS if k in data), None)
    return Quote(symbol=symbol, price=price, raw=data)


def get_orderbook(symbol: str) -> dict[str, Any]:
    """Return raw 호가 (bid/ask ladder) for a symbol."""
    res = request(tr.TR_ORDERBOOK, tr.EP_MRKCOND, {"stk_cd": symbol})
    return res.data


def get_daily_chart(symbol: str, base_dt: str) -> list[dict[str, Any]]:
    """일봉 차트 (ka10081). base_dt(YYYYMMDD) 기준 과거 일봉 배열을 그대로 반환.

    각 항목: dt, open_pric, high_pric, low_pric, cur_prc(종가), trde_qty, pred_pre.
    날짜 범위 필터링은 호출측(프론트)에서 수행한다.
    """
    res = request(
        tr.TR_DAILY_CHART,
        tr.EP_CHART,
        {"stk_cd": symbol, "base_dt": base_dt, "upd_stkpc_tp": "1"},
    )
    return res.data.get("stk_dt_pole_chart_qry", [])
