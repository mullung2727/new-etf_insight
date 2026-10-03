"""자동매매 배치 공용 broker REST·주문시간·수량 유틸리티."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests


REQUEST_TIMEOUT = 15

# 유증 전략 자금 분리 예약 (R2b). {"host:port": {"reserve", "capital_total", ...}}.
# 유증 verify·order 가 갱신, 다른 전략 available_cash 가 차감. 파일 없으면 기존 동작 그대로.
RESERVATION_PATH = Path(__file__).resolve().parents[1] / "db" / "cash_reservations.json"

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


def require_profile(broker_url: str, expected: str) -> bool:
    """broker ``/health`` 의 계좌 구분(profile)이 expected 인가.

    계좌마다 broker 를 따로 띄우므로, 러너가 다른 계좌 broker 에 주문하지 않게 주문 전에 확인한다.
    조회 실패도 False — 어느 계좌인지 모르면 주문하지 않는다.
    """
    try:
        response = requests.get(f"{broker_url}/health", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json().get("profile") == expected
    except Exception as error:
        print(f"[profile] /health 조회 실패: {error}")
        return False


def _broker_key(url: str) -> str:
    """broker_url → 예약 키 (host:port). localhost ≡ 127.0.0.1."""
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
        if host == "localhost":
            host = "127.0.0.1"
        port = parts.port
        return f"{host}:{port}" if port else host
    except Exception:
        return str(url)


def read_reservation(broker_url: str) -> dict | None:
    """계좌 예약 1건. 파일 없음·깨짐·키 없음 → None (예외 없음, 깨짐은 경고)."""
    try:
        raw = RESERVATION_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError as error:
        print(f"[reserve] 예약 파일 깨짐 — 예약 없음 취급 ({RESERVATION_PATH}: {error})")
        return None
    if not isinstance(data, dict):
        print(f"[reserve] 예약 파일 형식 오류 — 예약 없음 취급 ({RESERVATION_PATH})")
        return None
    entry = data.get(_broker_key(broker_url))
    return entry if isinstance(entry, dict) else None


def available_cash(broker_url: str, exclude_reserve: bool = True) -> int | None:
    """주문가능금액 — exclude_reserve 면 유증 예약 차감 (없으면 기존값 그대로)."""
    cash = _deposit_cash(broker_url)
    if cash is None or not exclude_reserve:
        return cash
    reservation = read_reservation(broker_url)
    if reservation is None:
        return cash
    try:
        reserve = int(reservation.get("reserve", 0))
    except (TypeError, ValueError):
        return cash
    return max(0, cash - reserve)


def _deposit_cash(broker_url: str) -> int | None:
    """GET /account/deposit → ord_alow_amt. 실패 시 None."""
    try:
        response = requests.get(f"{broker_url}/account/deposit", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        value = response.json().get("ord_alow_amt")
        return int(value) if value is not None else None
    except Exception:
        return None


def write_reservation(broker_url: str, reserve: int, capital_total: int, strategy: str) -> None:
    """예약 1건 저장 — tmp → os.replace 원자적, 다른 계좌 키 보존. 호출부는 P3(b)."""
    try:
        data = json.loads(RESERVATION_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[_broker_key(broker_url)] = {
        "reserve": int(reserve), "capital_total": int(capital_total),
        "strategy": strategy, "updated": now_seoul().isoformat(),
    }
    RESERVATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESERVATION_PATH.with_name(RESERVATION_PATH.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, RESERVATION_PATH)


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
            f"{broker_url}/orders/strategy",
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
    """키움 잔고의 ``A`` + 6자리 종목코드 접두를 벗긴다."""
    text = str(code).strip()
    return text[1:] if len(text) == 7 and text[:1] == "A" else text


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
