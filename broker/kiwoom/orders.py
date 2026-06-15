"""매수매도 — the only order entry point. Guards run before the wire call."""

from __future__ import annotations

from typing import Any

from . import tr
from .client import request
from .config import Config, load_config
from .guards import check_order
from .models import OrderRequest, OrderResult, OrderType, Side

_cfg: Config | None = None


def _config() -> Config:
    global _cfg
    if _cfg is None:
        _cfg = load_config()
    return _cfg

# Kiwoom 매매구분 (trade type) codes.
_TRDE_LIMIT = "0"   # 지정가
_TRDE_MARKET = "3"  # 시장가


def place_order(req: OrderRequest) -> OrderResult:
    """Submit a buy/sell order after enforcing the configured guards."""
    cfg = _config()
    market = req.order_type == OrderType.market
    check_order(cfg, qty=req.qty, price=req.price, market=market)

    api_id = tr.TR_ORDER_BUY if req.side == Side.buy else tr.TR_ORDER_SELL
    body = {
        "dmst_stex_tp": "KRX",
        "stk_cd": req.symbol,
        "ord_qty": str(req.qty),
        "ord_uv": "" if market else str(req.price),
        "trde_tp": _TRDE_MARKET if market else _TRDE_LIMIT,
    }
    res = request(api_id, tr.EP_ORDR, body)
    data = res.data
    order_no = data.get("ord_no") or data.get("odno")
    return OrderResult(
        accepted=True,
        order_no=str(order_no) if order_no else None,
        message=str(data.get("return_msg", "")),
        raw=data,
    )


def get_order_history(date: str) -> list[dict[str, Any]]:
    """kt00007 — 당일 매수 체결내역(상세)을 연속조회 병합해 반환한다.

    qry_tp=4(체결내역만), stk_bond_tp=1(주식), sell_tp=2(매수). 반환 리스트는
    원본 ``acnt_ord_cntr_prps_dtl`` 항목 그대로(정규화는 라우트 책임).
    """
    body = {
        "ord_dt": date,        # YYYYMMDD
        "qry_tp": "4",         # 체결내역만
        "stk_bond_tp": "1",    # 주식
        "sell_tp": "2",        # 매수
        "stk_cd": "",          # 전체 종목
        "fr_ord_no": "",       # 전체
        "dmst_stex_tp": "%",   # 전체 거래소 (Required)
    }
    res = request(tr.TR_CNTR_HIST, tr.EP_ACNT, body)
    rows: list[dict[str, Any]] = list(res.data.get("acnt_ord_cntr_prps_dtl") or [])
    while res.cont_yn == "Y" and res.next_key:
        res = request(
            tr.TR_CNTR_HIST, tr.EP_ACNT, body, cont_yn="Y", next_key=res.next_key
        )
        rows.extend(res.data.get("acnt_ord_cntr_prps_dtl") or [])
    return rows


def cancel_order(order_no: str, symbol: str, qty: int = 0) -> OrderResult:
    """Cancel a resting order. qty=0 cancels the full remaining quantity."""
    body = {
        "dmst_stex_tp": "KRX",
        "orig_ord_no": str(order_no),
        "stk_cd": symbol,
        "cncl_qty": str(qty) if qty else "0",
    }
    res = request(tr.TR_ORDER_CANCEL, tr.EP_ORDR, body)
    data = res.data
    return OrderResult(
        accepted=True,
        order_no=str(data.get("ord_no") or order_no),
        message=str(data.get("return_msg", "")),
        raw=data,
    )
