"""미국 시세조회 — 일봉 (usa06012)."""

from __future__ import annotations

from typing import Any

from .client import request

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


def get_us_daily(code: str, from_date: str, today_ny: str) -> list[dict]:
    """미국 종목 일봉 (usa06012). strt_dt=today_ny부터 과거로 최대 100행/페이지.

    거래소는 캐시된 곳 먼저, 없으면 ND→NY→NA 순으로 빈 응답이 아닐 때까지 조회.
    끝난 날만 반환 — today_ny 당일 진행 봉·from_date 이전·종가 없는 행 제외, 날짜 오름차순.
    수정주가(upd_stkpc_tp=1), 원화환산 미적용(exrt_appl_tp=0, USD).
    """
    first = _EXCHANGE.get(code)
    order = [first, *[e for e in EXCHANGES if e != first]] if first else list(EXCHANGES)
    for ex in order:
        body = {"stex_tp": ex, "stk_cd": code, "strt_dt": today_ny,
                "upd_stkpc_tp": "1", "exrt_appl_tp": "0"}
        res = request("usa06012", "/api/us/chart", body)
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
            try:
                vol = int(str(r.get("acc_trde_qty")).replace("+", "").replace(",", "").strip())
            except (ValueError, TypeError):
                vol = None
            out.append({"date": dt, "open": _num(r.get("open_pric")),
                        "high": _num(r.get("high_pric")), "low": _num(r.get("low_pric")),
                        "close": close, "volume": vol})
    out.sort(key=lambda r: r["date"])
    return out
