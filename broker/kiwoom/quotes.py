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
