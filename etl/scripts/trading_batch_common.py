"""자동매매 배치 공용 broker REST·주문시간·수량 유틸리티."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import requests


REQUEST_TIMEOUT = 15


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
