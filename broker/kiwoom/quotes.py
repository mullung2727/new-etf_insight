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

# ka10095 1콜당 종목수 상한이 문서에 없어 보수적으로 분할(50). 실측 후 조정.
_WATCHLIST_CHUNK = 50


def _to_float(val: Any) -> float | None:
    """등락율 등 부호 포함 소수 문자열 → float ('+28.68' → 28.68)."""
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace("+", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _abs_int(val: Any) -> int | None:
    """가격류 정규화 — ka10095 값은 부호가 등락방향이라 절대값이 실가격."""
    n = _to_int(val)
    return abs(n) if n is not None else None


def _strip_code(code: str) -> str:
    """ka10095는 보통 'A' 접두 없이 오지만 방어적으로 제거."""
    code = str(code).strip()
    return code[1:] if code[:1] == "A" and code[1:].isdigit() else code


def get_quote(symbol: str) -> Quote:
    """Return current price for a 6-digit symbol code (예: '005930')."""
    res = request(tr.TR_STOCK_INFO, tr.EP_STKINFO, {"stk_cd": symbol})
    data = res.data
    price = next((_to_int(data[k]) for k in _PRICE_KEYS if k in data), None)
    return Quote(symbol=symbol, price=price, raw=data)


def get_watchlist_quotes(codes: list[str]) -> list[dict[str, Any]]:
    """복수종목 일괄시세 (ka10095). 관심종목 등록 불요·stateless — 매 콜에 코드 전달.

    codes를 `|`로 조인해 1콜에 N종목 조회. 상한 미확정이라 _WATCHLIST_CHUNK 단위 분할콜.
    가격류는 부호 제거 abs int, 등락율은 float, pred_pre_sig는 원본 문자열 유지.
    """
    cleaned = [c.strip() for c in codes if c and c.strip()]
    if not cleaned:
        return []

    out: list[dict[str, Any]] = []
    for i in range(0, len(cleaned), _WATCHLIST_CHUNK):
        chunk = cleaned[i : i + _WATCHLIST_CHUNK]
        res = request(
            tr.TR_WATCHLIST_QUOTE, tr.EP_STKINFO, {"stk_cd": "|".join(chunk)}
        )
        for it in res.data.get("atn_stk_infr", []) or []:
            out.append(
                {
                    "stk_cd": _strip_code(it.get("stk_cd", "")),
                    "cur_prc": _abs_int(it.get("cur_prc")),
                    "buy_bid": _abs_int(it.get("buy_bid")),
                    "sel_bid": _abs_int(it.get("sel_bid")),
                    "flu_rt": _to_float(it.get("flu_rt")),
                    "upl_pric": _abs_int(it.get("upl_pric")),
                    "lst_pric": _abs_int(it.get("lst_pric")),
                    "exp_cntr_pric": _abs_int(it.get("exp_cntr_pric")),
                    "pred_pre_sig": str(it.get("pred_pre_sig", "")).strip(),
                    "raw": it,
                }
            )
    return out


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
