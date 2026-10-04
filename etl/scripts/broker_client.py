"""실매매 전략들의 broker REST 호출을 한곳에 모은 공용 클라이언트.

여러 비공개 전략이 복사해 쓰던 호가 조회·추격 지정가·전략 주문·정정·취소·
미체결·체결내역 호출을 일반화했다. 전략별 차이는 log_tag(로그 접두)와
sources(side → 주문 source 매핑) 뿐이다. 전략 규칙·파라미터는 두지 않는다.

반환 status 어휘:
  submitted — broker 접수됨. order_no 보유.
  rejected  — broker가 거부함 (accepted 거짓). 사유 확인 후 판단.
  failed    — 접수 안 됨이 확정적임 (거부·형식 오류·조회 실패 등).
  unknown   — 예외로 접수 여부를 모름. 이미 접수됐을 수 있음 → 재주문(재정정)
              금지. 미체결/체결내역으로 먼저 확인할 것.
  dry_run   — dry_run 모드. HTTP 호출 없음 (order_no는 DRY_{종목}_{HHMMSS}).
  cancelled — 취소 접수됨.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.backtest_minute.ticks import tick_below, tick_size  # noqa: E402

try:
    from scripts.run_verify import normalize_order_no
except ImportError:  # etl/scripts 직접 실행 시
    from run_verify import normalize_order_no  # noqa: E402

__all__ = ["BrokerClient", "fills_by_order", "quote_fresh"]

KST = ZoneInfo("Asia/Seoul")

_QUOTE_FIELD = {"buy": "sel_fpr_bid", "sell": "buy_fpr_bid"}


def _now_seoul() -> datetime:
    return datetime.now(KST)


def quote_fresh(base_tm: str, now: datetime, max_age_s: int) -> bool:
    """bid_req_base_tm(HHMMSS)이 max_age_s 안에 드는지 판정.

    6자리 숫자가 아니면(없음 포함) 신선한 것으로 본다.
    """
    s = str(base_tm or "").strip()
    if len(s) != 6 or not s.isdigit():
        return True
    try:
        base = now.replace(hour=int(s[0:2]), minute=int(s[2:4]),
                           second=int(s[4:6]), microsecond=0)
    except ValueError:
        return True
    return (now - base).total_seconds() <= max_age_s


def fills_by_order(rows: list[dict] | None) -> dict[str, dict] | None:
    """history 행 → {정규화 order_no: {"cntr_qty": 합산}}. None은 그대로 None."""
    if rows is None:
        return None
    by_no: dict[str, dict] = {}
    for item in rows:
        key = normalize_order_no(item.get("order_no"))
        if not key:
            continue
        agg = by_no.setdefault(key, {"cntr_qty": 0})
        agg["cntr_qty"] += int(item.get("cntr_qty") or 0)
    return by_no


def _parse_quote(raw: object) -> int:
    """호가값 파싱. 문자열(콤마·부호) → 절댓값 정수."""
    return abs(int(str(raw).replace(",", "").strip()))


class BrokerClient:
    """broker REST 공용 클라이언트.

    broker_url: broker 주소 (예 http://127.0.0.1:8001).
    sources: side → 주문 source 매핑 (예 {"buy": "xxx_buy", "sell": "xxx_sell"}).
    """

    def __init__(self, broker_url: str, *, sources: dict[str, str],
                 exchange: str = "KRX", dry_run: bool = False,
                 log_tag: str = "broker", timeout: float = 15,
                 max_quote_age_s: int = 300) -> None:
        self._url = broker_url.rstrip("/")
        self._sources = dict(sources)
        self._exchange = exchange
        self._dry_run = dry_run
        self._tag = log_tag
        self._timeout = timeout
        self._max_quote_age_s = max_quote_age_s

    def _dry_no(self, t: str) -> str:
        return f"DRY_{t}_{_now_seoul().strftime('%H%M%S')}"

    def best_quote(self, t: str, side: str) -> int | None:
        """매도1호가(buy)/매수1호가(sell) 원값. 없거나 오래되면 None."""
        field = _QUOTE_FIELD.get(side)
        if field is None:
            print(f"[{self._tag}] {t} best_quote side 오류: {side!r}")
            return None
        try:
            resp = requests.get(f"{self._url}/quotes/{t}/orderbook",
                                timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("raw")
            if not isinstance(raw, dict):
                raw = {}
            base_tm = data.get("bid_req_base_tm")
            if base_tm is None:
                base_tm = raw.get("bid_req_base_tm")
            if not quote_fresh(base_tm, _now_seoul(), self._max_quote_age_s):
                print(f"[{self._tag}] {t} 호가 오래됨(base_tm={base_tm}) — 무시")
                return None
            value = data.get(field)
            if value is None:
                value = raw.get(field)
            px = _parse_quote(value)
            if px <= 0:
                print(f"[{self._tag}] {t} {side} 호가 없음(0)")
                return None
            return px
        except Exception as exc:
            print(f"[{self._tag}] {t} 호가 조회 실패: {exc}")
            return None

    def limit_price(self, t: str, side: str) -> int | None:
        """추격 지정가. buy = 매도1호가 − tick_below, sell = 매수1호가 + tick_size."""
        quote = self.best_quote(t, side)
        if quote is None:
            return None
        if side == "buy":
            px = quote - tick_below(quote)
        elif side == "sell":
            px = quote + tick_size(quote)
        else:
            return None  # best_quote가 이미 로그
        if px <= 0:
            print(f"[{self._tag}] {t} 추격 지정가 0 이하({px}) — 포기")
            return None
        return px

    def place(self, t: str, side: str, qty: int, price: int,
              order_type: str) -> dict:
        """전략 주문. 시장가(order_type="market")는 price 0으로 보낸다."""
        source = self._sources.get(side)
        if source is None:
            msg = f"미등록 side: {side!r}"
            print(f"[{self._tag}] {t} {msg}")
            return {"order_no": "", "status": "failed", "message": msg}
        if order_type == "market":
            price = 0
        if self._dry_run:
            return {"order_no": self._dry_no(t), "status": "dry_run"}
        try:
            resp = requests.post(
                f"{self._url}/orders/strategy",
                json={"symbol": t, "side": side, "qty": qty, "price": price,
                      "order_type": order_type, "source": source,
                      "exchange": self._exchange},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("accepted"):
                msg = data.get("message", "")
                print(f"[{self._tag}] {t} 주문 거부: {msg}")
                return {"order_no": "", "status": "rejected", "message": msg}
            order_no = data.get("order_no")
            if not order_no:
                msg = data.get("message", "")
                print(f"[{self._tag}] {t} 주문 실패(order_no 없음): {msg}")
                return {"order_no": "", "status": "failed", "message": msg}
            order_no = str(order_no)
            print(f"[{self._tag}] {t} 주문 접수: {order_no}")
            return {"order_no": order_no, "status": "submitted",
                    "message": data.get("message", "")}
        except Exception as exc:
            print(f"[{self._tag}] {t} 주문 예외 — 접수됐을 수 있음, 재주문 금지: {exc}")
            return {"order_no": "", "status": "unknown", "message": str(exc)}

    def place_limit(self, t: str, side: str, qty: int, price: int) -> dict:
        """지정가 주문."""
        return self.place(t, side, qty, price, "limit")

    def place_market(self, t: str, side: str, qty: int) -> dict:
        """시장가 주문 (price 0)."""
        return self.place(t, side, qty, 0, "market")

    def modify(self, no: str, t: str, price: int, qty: int) -> dict:
        """정정. 성공 시 새 주문번호로 submitted."""
        if self._dry_run:
            return {"order_no": self._dry_no(t), "status": "dry_run"}
        try:
            resp = requests.patch(
                f"{self._url}/orders/{no}",
                json={"symbol": t, "price": price, "qty": qty,
                      "exchange": self._exchange},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("accepted") is False:
                msg = data.get("message", "")
                print(f"[{self._tag}] {no} 정정 거부: {msg}")
                return {"order_no": no, "status": "failed", "message": msg}
            new_no = data.get("order_no")
            if not new_no or normalize_order_no(new_no) == normalize_order_no(no):
                msg = "정정 후 새 주문번호 없음"
                print(f"[{self._tag}] {no} 정정 실패: {msg}")
                return {"order_no": no, "status": "failed", "message": msg}
            new_no = str(new_no)
            print(f"[{self._tag}] {no} 정정 접수: {new_no}")
            return {"order_no": new_no, "status": "submitted",
                    "message": data.get("message", "")}
        except Exception as exc:
            print(f"[{self._tag}] {no} 정정 예외 — 접수됐을 수 있음: {exc}")
            return {"order_no": no, "status": "unknown", "message": str(exc)}

    def cancel(self, no: str, t: str) -> dict:
        """취소. 빈 본문도 cancelled."""
        if self._dry_run:
            return {"order_no": self._dry_no(t), "status": "dry_run"}
        try:
            resp = requests.delete(
                f"{self._url}/orders/{no}",
                params={"symbol": t, "qty": 0, "exchange": self._exchange},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            try:
                data = resp.json()
            except Exception:
                data = None
            if isinstance(data, dict) and data.get("accepted") is False:
                msg = data.get("message", "")
                print(f"[{self._tag}] {no} 취소 거부: {msg}")
                return {"order_no": no, "status": "failed", "message": msg}
            print(f"[{self._tag}] {no} 취소 접수")
            return {"order_no": no, "status": "cancelled"}
        except Exception as exc:
            print(f"[{self._tag}] {no} 취소 실패: {exc}")
            return {"order_no": no, "status": "failed", "message": str(exc)}

    def unfilled(self, side: str) -> list[dict] | None:
        """미체결 목록. list가 아니면 None."""
        try:
            resp = requests.get(f"{self._url}/orders/unfilled",
                                params={"side": side}, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                print(f"[{self._tag}] 미체결 응답 형식 오류(list 아님)")
                return None
            return data
        except Exception as exc:
            print(f"[{self._tag}] 미체결 조회 실패: {exc}")
            return None

    def history(self, date: str, side: str) -> list[dict] | None:
        """체결내역 목록. list가 아니면 None."""
        try:
            resp = requests.get(f"{self._url}/orders/history",
                                params={"date": date, "side": side},
                                timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                print(f"[{self._tag}] 체결내역 응답 형식 오류(list 아님)")
                return None
            return data
        except Exception as exc:
            print(f"[{self._tag}] 체결내역 조회 실패: {exc}")
            return None
