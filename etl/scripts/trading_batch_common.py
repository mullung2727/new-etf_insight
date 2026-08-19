"""자동매매 배치 공용 broker REST·주문시간·수량 유틸리티."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests


REQUEST_TIMEOUT = 15

# 청산이 끝난 것으로 간주하는 sell_status. 청산 워커의 미청산 조회와 주문 배치의
# 중복매수 가드가 같은 집합을 봐야 한다 — 한쪽만 알면 유령 포지션이 매수를 영구 차단한다.
#   filled  = 실제 매도 체결
#   missing = 매수 기록은 있으나 계좌 잔고에 없음(모의계좌 리셋 등). 매도할 물량이
#             없으므로 청산 불가 — 조용히 건너뛰지 말고 종료로 확정한다.
CLOSED_SELL_STATUSES = ("filled", "missing")


def now_seoul() -> datetime:
    return datetime.now(ZoneInfo("Asia/Seoul"))


def in_order_window(now: datetime, start: str, deadline: str) -> bool:
    def at(value: str) -> datetime:
        hour, minute, second = (int(part) for part in value.split(":"))
        return now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    return at(start) <= now < at(deadline)


def current_price(broker_url: str, ticker: str) -> int | None:
    try:
        response = requests.get(f"{broker_url}/quotes/{ticker}", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        value = response.json().get("price")
        return int(value) if value is not None else None
    except Exception:
        return None


def quote_snapshot(broker_url: str, ticker: str) -> dict[str, int] | None:
    try:
        response = requests.get(f"{broker_url}/quotes/{ticker}", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        raw = data.get("raw") or {}
        price = data.get("price")
        day_open = raw.get("open_pric")
        day_low = raw.get("low_pric")
        if any(value is None for value in (price, day_open, day_low)):
            return None
        return {"current_price": abs(int(price)), "open": abs(int(day_open)), "low": abs(int(day_low))}
    except Exception:
        return None


def available_cash(broker_url: str) -> int | None:
    try:
        response = requests.get(f"{broker_url}/account/deposit", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        value = response.json().get("ord_alow_amt")
        return int(value) if value is not None else None
    except Exception:
        return None


def fetch_realized(broker_url: str, ticker: str) -> dict | None:
    """GET /orders/realized/{ticker} → net 실현손익(키움 권위값). 실패/미발견 시 None.

    당일 매도 체결 후 호출해 수수료·세금 차감된 pnl_pct·손익금을 받는다.
    키움 pnl_pct는 %(예: -4.84) — 분수 규약 DB에 넣을 땐 호출부에서 /100.
    """
    try:
        response = requests.get(
            f"{broker_url}/orders/realized/{ticker}", timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return data if data.get("found") else None
    except Exception as error:
        print(f"[realized] /orders/realized 조회 실패({ticker}): {error}")
        return None


def market_order(
    broker_url: str, ticker: str, qty: int, side: str, source: str, dry_run: bool,
    *, now: datetime | None = None,
) -> dict:
    if dry_run:
        timestamp = (now or now_seoul()).strftime("%Y%m%d%H%M%S")
        return {"order_no": f"DRY_{ticker}_{timestamp}", "status": "dry_run",
                "message": "dry_run — 실제 주문 없음"}
    try:
        response = requests.post(
            f"{broker_url}/orders",
            json={"symbol": ticker, "side": side, "qty": qty,
                  "order_type": "market", "source": source},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code == 422:
            detail = response.json().get("detail", "rejected")
            return {"order_no": "", "status": "rejected", "message": detail}
        response.raise_for_status()
        data = response.json()
        if not data.get("accepted"):
            return {"order_no": "", "status": "failed",
                    "message": data.get("message", "accepted=False")}
        return {"order_no": str(data.get("order_no") or ""), "status": "submitted",
                "message": data.get("message", "")}
    except Exception as error:
        return {"order_no": "", "status": "failed", "message": str(error)}


def quantity_for_budget(budget: int, price: int) -> int:
    return budget // price if budget > 0 and price > 0 else 0


def _strip_ticker(code: object) -> str:
    """키움 잔고의 'A005930' 접두를 벗긴다."""
    text = str(code).strip()
    return text[1:] if text[:1] == "A" and text[1:].isdigit() else text


def _padint(value: object) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return 0


def held_quantities(balance: dict) -> dict[str, int] | None:
    """잔고 응답 → {종목코드: 매도가능수량}.

    조회 실패·응답 이상이면 None. 이걸 빈 dict로 뭉개면 호출부가 '전 종목 미보유'로
    읽어서 멀쩡한 포지션까지 유령으로 마감해버린다. 보유 0건인 정상 응답({})과
    반드시 구분해야 한다.
    """
    if not isinstance(balance, dict):
        return None
    rows = balance.get("acnt_evlt_remn_indv_tot")
    if not isinstance(rows, list):
        return None
    return {_strip_ticker(r.get("stk_cd", "")): _padint(r.get("trde_able_qty")) for r in rows}
