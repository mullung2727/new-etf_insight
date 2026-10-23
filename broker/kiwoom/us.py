"""미국주식 시세·잔고·주문 (usa06012·usa20101·ust20000/20001·ust21070·ust21110·ust21510)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .client import KiwoomError, TrResult, request

# 키움 usa06012 stex_tp: ND 나스닥, NY NYSE, NA AMEX.
# 실측 2026-10-22: QQQ·TQQQ·IEF=ND, SPY·BIL=NY(NYSE Arca도 NY로 조회됨).
EXCHANGES = ("ND", "NY", "NA")

# 종목별로 응답이 있었던 거래소 기억. broker는 단일 프로세스라 모듈 전역으로 충분
# (quotes 라우터 TTL 캐시와 같은 이유).
_EXCHANGE: dict[str, str] = {}

_MAX_PAGES = 30  # 연속조회 안전장치 — 100행/페이지라 일봉 용도로 충분


def _num(s: Any) -> float | None:
    """부호 포함 숫자 문자열 → float ('+1.3212' → 1.3212). 빈값·변환 실패는 None."""
    if s is None or s == "":
        return None
    try:
        return float(str(s).replace("+", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _abs(v: float | None) -> float | None:
    """None은 그대로, 값은 절대값."""
    return None if v is None else abs(v)


def _int(s: Any) -> int | None:
    """0-padding 수량 문자열("000000000395") → int. 빈값·변환 실패는 None."""
    v = _num(s)
    return None if v is None else int(v)


def _exchange_order(code: str) -> list[str]:
    """캐시된 거래소 먼저, 나머지는 EXCHANGES 순."""
    first = _EXCHANGE.get(code)
    return [first, *[e for e in EXCHANGES if e != first]] if first else list(EXCHANGES)


def _is_no_symbol(exc: KiwoomError) -> bool:
    """거래소 불일치 판정 — 실측 2026-10-23: return_code=7 [1903:종목 정보가 없습니다]."""
    msg = str(exc)
    return "return_code=7" in msg and "[1903:" in msg


def _call_with_exchange(
    api_id: str, endpoint: str, body_fn: Callable[[str], dict[str, Any]], code: str
) -> TrResult:
    """거래소 순서대로 request 호출, 1903이면 다음 거래소로. 성공한 거래소 캐시.

    전부 1903이면 마지막 KiwoomError를 그대로 올린다. 1903 외 에러는 즉시 전파.
    """
    last: KiwoomError | None = None
    for ex in _exchange_order(code):
        try:
            res = request(api_id, endpoint, body_fn(ex))
        except KiwoomError as exc:
            if not _is_no_symbol(exc):
                raise
            if _EXCHANGE.get(code) == ex:
                del _EXCHANGE[code]
            last = exc
            continue
        _EXCHANGE[code] = ex
        return res
    assert last is not None  # EXCHANGES가 비지 않아 1903이 최소 하나 잡힘
    raise last


def get_us_daily(code: str, from_date: str, today_ny: str) -> list[dict]:
    """미국 종목 일봉 (usa06012). strt_dt=today_ny부터 과거로 최대 100행/페이지.

    거래소는 캐시된 곳 먼저, 없으면 ND→NY→NA 순으로 빈 응답이 아닐 때까지 조회.
    끝난 날만 반환 — today_ny 당일 진행 봉·from_date 이전·종가 없는 행 제외, 날짜 오름차순.
    수정주가(upd_stkpc_tp=1), 원화환산 미적용(exrt_appl_tp=0, USD).
    """
    for ex in _exchange_order(code):
        body = {"stex_tp": ex, "stk_cd": code, "strt_dt": today_ny,
                "upd_stkpc_tp": "1", "exrt_appl_tp": "0"}
        # 거래소를 틀리게 주면 빈 응답이 아니라 return_code=7 [1903:종목 정보가 없습니다] KiwoomError.
        try:
            res = request("usa06012", "/api/us/chart", body)
        except KiwoomError as e:
            if not _is_no_symbol(e):
                raise
            if _EXCHANGE.get(code) == ex:
                del _EXCHANGE[code]
            continue
        rows = res.data.get("result_list") or []
        if not rows:
            continue
        _EXCHANGE[code] = ex
        pages = [rows]
        cont_yn, next_key = res.cont_yn, res.next_key
        while cont_yn == "Y" and len(pages) < _MAX_PAGES:
            oldest = min((str(r.get("dt") or "") for p in pages for r in p), default="")
            if oldest <= from_date:  # from에 닿았으면 더 과거는 불요
                break
            res = request("usa06012", "/api/us/chart", body, cont_yn="Y", next_key=next_key)
            rows = res.data.get("result_list") or []
            if not rows:
                break
            pages.append(rows)
            cont_yn, next_key = res.cont_yn, res.next_key
        break
    else:
        return []

    out: list[dict] = []
    for page in pages:
        for r in page:
            dt = str(r.get("dt") or "").strip()
            if not dt or dt >= today_ny or dt < from_date:
                continue
            close = _num(r.get("cur_prc"))
            if close is None:
                continue
            out.append({"date": dt, "open": _num(r.get("open_pric")),
                        "high": _num(r.get("high_pric")), "low": _num(r.get("low_pric")),
                        "close": close, "volume": _int(r.get("acc_trde_qty"))})
    out.sort(key=lambda r: r["date"])
    return out


def get_us_quote(code: str) -> dict:
    """미국 현재가 10호가 (usa20101). 거래소 1903 폴백·캐시는 _call_with_exchange."""
    res = _call_with_exchange(
        "usa20101", "/api/us/mrkcond",
        lambda ex: {"stex_tp": ex, "stk_cd": code}, code,
    )
    data = res.data
    # usa20101 cur_prc·호가는 전일 대비 부호가 붙어 온다(실측 2026-10-23: "-705.14") → 가격은 절대값.
    return {"code": code, "exchange": _EXCHANGE[code],
            "price": _abs(_num(data.get("cur_prc"))),
            "bid1": _abs(_num(data.get("buy_1bid"))), "ask1": _abs(_num(data.get("sel_1bid"))),
            "volume": _int(data.get("trde_qty"))}


def get_us_balance() -> list[dict]:
    """미국 원장잔고 (ust21070). {"stex_tp": "", "stk_cd": ""} 1회 호출로 보유 전 종목.

    실측 2026-10-23: 빈 값 1회면 보유 전 종목이 오고 행에 거래소 코드 없음(stex_nm="미국").
    stex_tp를 ND 등으로 주고 stk_cd를 비우면 1517 종목 코드값이 없습니다 오류.
    행 exchange는 _EXCHANGE 캐시에 있으면 그 값, 없으면 None.
    수량 0 행은 제외. 금액·단가는 USD.
    """
    res = request("ust21070", "/api/us/acnt", {"stex_tp": "", "stk_cd": ""})
    out: list[dict] = []
    for r in res.data.get("result_list") or []:
        qty = _int(r.get("poss_qty"))
        if not qty:
            continue
        code = str(r.get("stk_cd") or "").strip()
        out.append({"code": code, "exchange": _EXCHANGE.get(code),
                    "qty": qty, "sellable_qty": _int(r.get("sell_alowq")),
                    "avg_price": _num(r.get("frgn_stk_book_uv")),
                    "last_price": _num(r.get("now_pric")),
                    "eval_amt": _num(r.get("evlt_amt"))})
    return out


def get_us_deposit() -> dict:
    """해외주식 예수금 (ust21110). crnc_code=='USD' 행에서 외화예수금·주문가능·출금가능."""
    res = request("ust21110", "/api/us/acnt", {})
    for r in res.data.get("result_list") or []:
        if str(r.get("crnc_code") or "").strip() == "USD":
            return {"usd_cash": _num(r.get("fc_entra")) or 0,
                    "usd_orderable": _num(r.get("fc_ord_alowa")) or 0,
                    "usd_withdrawable": _num(r.get("fc_pymn_alowa")) or 0}
    return {"usd_cash": 0, "usd_orderable": 0, "usd_withdrawable": 0}


_SIDE_BY_SB = {"1": "sell", "2": "buy"}  # ust21510 slby_tp: 1 매도, 2 매수


def get_us_fills() -> list[dict]:
    """미국 당일 주문체결 확인 (ust21510). 전체 구분(slby_tp=0)·전체 종목."""
    res = request("ust21510", "/api/us/acnt", {"slby_tp": "0", "stex_tp": "", "stk_cd": ""})
    out: list[dict] = []
    for r in res.data.get("result_list") or []:
        out.append({"order_no": str(r.get("ord_no") or "").strip(),
                    "code": str(r.get("stk_cd") or "").strip(),
                    "side": _SIDE_BY_SB.get(str(r.get("slby_tp") or "").strip()),
                    "qty": _int(r.get("ord_qty")),
                    "filled_qty": _int(r.get("cntr_qty")),
                    "avg_fill_price": _num(r.get("cntr_uv")),
                    "status": str(r.get("ord_stat") or "").strip() or None})
    return out


def place_us_order(code: str, side: str, qty: int, trde_tp: str, price: float | None) -> dict:
    """미국 매수(ust20000)/매도(ust20001). 시장가(03)면 ord_uv 빈 문자열."""
    api_id = "ust20000" if side == "buy" else "ust20001"
    res = _call_with_exchange(
        api_id, "/api/us/ordr",
        lambda ex: {"stex_tp": ex, "stk_cd": code, "ord_qty": str(qty),
                    "ord_uv": "" if trde_tp == "03" else str(price),
                    "trde_tp": trde_tp},
        code,
    )
    data = res.data
    order_no = data.get("ord_no")
    return {"order_no": str(order_no) if order_no else "", "raw": data}
