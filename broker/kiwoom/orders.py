"""매수매도 — the only order entry point. Guards run before the wire call."""

from __future__ import annotations

from typing import Any

from . import quotes, tr
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


def _est_price(symbol: str) -> int | None:
    """시장가 매수 금액가드용 현재가. 조회 실패는 None — 가드가 주문을 거부한다."""
    try:
        return quotes.get_quote(symbol).price
    except Exception:  # noqa: BLE001 — 조회 실패는 곧 거부, 원인 구분 불필요
        return None


def place_order(
    req: OrderRequest, *, enforce_amount_cap: bool
) -> OrderResult:
    """Submit an order using the server-route policy supplied by the caller."""
    cfg = _config()
    market = req.order_type == OrderType.market
    # 정책은 request body의 source가 아니라 수동/전략 서버 라우트가 결정한다.
    # 자동 시장가 매수만 상한 계산용 현재가 1콜 추가. 수동 매수와 매도는 조회하지 않는다.
    est = (
        _est_price(req.symbol)
        if enforce_amount_cap and market and req.side == Side.buy
        else None
    )
    check_order(
        cfg, qty=req.qty, price=req.price, market=market,
        side=req.side.value, est_price=est,
        enforce_amount_cap=enforce_amount_cap,
    )

    api_id = tr.TR_ORDER_BUY if req.side == Side.buy else tr.TR_ORDER_SELL
    body = {
        "dmst_stex_tp": "SOR",
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


def get_order_history(date: str, sell_tp: str = "2") -> list[dict[str, Any]]:
    """kt00007 — 당일 체결내역(상세)을 연속조회 병합해 반환한다.

    qry_tp=4(체결내역만), stk_bond_tp=1(주식). sell_tp: 1=매도, 2=매수(기본·체결대조용).
    매도 청산 체결확인은 sell_tp="1"로 호출. 반환 리스트는 원본
    ``acnt_ord_cntr_prps_dtl`` 항목 그대로(정규화는 라우트 책임).
    """
    body = {
        "ord_dt": date,        # YYYYMMDD
        "qry_tp": "4",         # 체결내역만
        "stk_bond_tp": "1",    # 주식
        "sell_tp": sell_tp,    # 1=매도 / 2=매수
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


_TRDE_TP_BY_SIDE = {"sell": "1", "buy": "2", "all": "0"}


def get_unfilled(side: str = "sell") -> list[dict[str, Any]]:
    """ka10075 — 미체결 주문 목록을 연속조회 병합해 반환한다.

    side: sell(매도)/buy(매수)/all(전체). 청산 체결확인은 미체결에서 우리 매도주문이
    사라졌는지로 판단한다. 반환 리스트는 원본 ``oso`` 항목 그대로(정규화는 라우트 책임).
    """
    trde_tp = _TRDE_TP_BY_SIDE.get(side, "0")
    body = {
        "all_stk_tp": "0",   # 0:전체 종목
        "trde_tp": trde_tp,  # 0:전체 1:매도 2:매수
        "stk_cd": "",        # 전체 종목
        "stex_tp": "0",      # 0:통합
    }
    res = request(tr.TR_UNFILLED, tr.EP_ACNT, body)
    rows: list[dict[str, Any]] = list(res.data.get("oso") or [])
    while res.cont_yn == "Y" and res.next_key:
        res = request(tr.TR_UNFILLED, tr.EP_ACNT, body, cont_yn="Y", next_key=res.next_key)
        rows.extend(res.data.get("oso") or [])
    return rows


def get_today_realized(symbol: str) -> list[dict[str, Any]]:
    """ka10077 — 당일실현손익상세를 연속조회 병합해 반환한다.

    매도 체결 후 호출하면 키움이 수수료·세금을 차감한 net 손익(``tdy_sel_pl`` 원,
    ``pl_rt`` %)을 종목별로 준다. 반환 리스트는 원본 ``tdy_rlzt_pl_dtl`` 항목 그대로
    (정규화는 라우트 책임). 당일분만 조회되므로 매도 당일에 호출해야 한다.
    """
    body = {"stk_cd": symbol}
    res = request(tr.TR_RLZT_PL_TODAY, tr.EP_ACNT, body)
    rows: list[dict[str, Any]] = list(res.data.get("tdy_rlzt_pl_dtl") or [])
    while res.cont_yn == "Y" and res.next_key:
        res = request(
            tr.TR_RLZT_PL_TODAY, tr.EP_ACNT, body, cont_yn="Y", next_key=res.next_key
        )
        rows.extend(res.data.get("tdy_rlzt_pl_dtl") or [])
    return rows


def get_realized_by_date(symbol: str, date: str) -> list[dict[str, Any]]:
    """ka10072 — 특정 일자 종목 실현손익. 필드는 ka10077과 동일(``tdy_sel_pl``·
    ``pl_rt``·``tdy_trde_cmsn``·``tdy_trde_tax``). 당일限인 ka10077로 못 가져오는
    과거 청산분 소급 백필용. date=YYYYMMDD. 모의 도메인도 지원(KRX限).
    """
    body = {"stk_cd": symbol, "strt_dt": date}
    res = request(tr.TR_RLZT_PL_DATE, tr.EP_ACNT, body)
    rows: list[dict[str, Any]] = list(res.data.get("dt_stk_div_rlzt_pl") or [])
    while res.cont_yn == "Y" and res.next_key:
        res = request(
            tr.TR_RLZT_PL_DATE, tr.EP_ACNT, body, cont_yn="Y", next_key=res.next_key
        )
        rows.extend(res.data.get("dt_stk_div_rlzt_pl") or [])
    return rows


def modify_order(order_no: str, symbol: str, price: int, qty: int = 0) -> OrderResult:
    """kt10002 — 미체결 주문 정정. qty=0이면 잔량 전부 정정. price=정정단가."""
    body = {
        "dmst_stex_tp": "SOR",
        "orig_ord_no": str(order_no),
        "stk_cd": symbol,
        "mdfy_qty": str(qty) if qty else "0",
        "mdfy_uv": str(price),
    }
    res = request(tr.TR_ORDER_MODIFY, tr.EP_ORDR, body)
    data = res.data
    return OrderResult(
        accepted=True,
        order_no=str(data.get("ord_no") or order_no),
        message=str(data.get("return_msg", "")),
        raw=data,
    )


def cancel_order(order_no: str, symbol: str, qty: int = 0) -> OrderResult:
    """Cancel a resting order. qty=0 cancels the full remaining quantity."""
    body = {
        "dmst_stex_tp": "SOR",
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
