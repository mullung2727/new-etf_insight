"""Order guards — structural validation plus strategy-buy hard caps.

All orders flow through ``orders.place_order`` which calls ``check_order`` here.
``MAX_ORDER_AMOUNT`` is enforced for automatic strategy buys; manual orders keep
structural validation but are intentionally exempt from that strategy cap.
"""

from __future__ import annotations

import math
import os

from .config import Config


class OrderRejected(Exception):
    """Raised when an order violates a configured guard. Never sent to Kiwoom."""


def check_order(
    cfg: Config,
    *,
    qty: int,
    price: int,
    market: bool,
    side: str,
    est_price: int | None = None,
    enforce_amount_cap: bool = True,
) -> None:
    if qty <= 0:
        raise OrderRejected(f"수량은 1 이상이어야 함: qty={qty}")

    # 금액 상한 면제 여부와 무관하게 모든 지정가는 양수 가격을 요구한다.
    if not market and price <= 0:
        raise OrderRejected(f"지정가 주문은 price>0 필요: price={price}")

    # 매도는 형식 검증 후 금액 상한만 면제한다. 보유수량 초과는 키움이 거부(800033)하고,
    # 상한이 매도를 막으면 급등 종목의 손절/강제청산이 거부돼 포지션이 갇힌다.
    if side == "sell":
        return

    # 수동 라우트는 형식 검증만 유지하고 전략용 금액 상한은 적용하지 않는다.
    if not enforce_amount_cap:
        return

    if not market:
        unit = price
    else:
        # 자동매매 시장가는 주문에 가격이 없으므로 호출부가 넘긴 현재가로 금액을 추정한다.
        # 추정 불가면 거부(fail-closed) — 상한 없는 자동주문이 나가는 것보다 안전하다.
        if not est_price or est_price <= 0:
            raise OrderRejected(
                "시장가 주문 예상금액 확인 실패 — 현재가 조회 불가. 주문을 보내지 않음"
            )
        unit = est_price

    notional = qty * unit
    if notional > cfg.max_order_amount:
        kind = "시장가 예상금액" if market else "주문금액"
        raise OrderRejected(
            f"{kind} 상한 초과: {notional:,} > MAX_ORDER_AMOUNT="
            f"{cfg.max_order_amount:,}"
        )


def check_order_source(source: str) -> None:
    """ALLOWED_ORDER_SOURCES 가드 — 허용 목록 밖 source는 ValueError.

    국내·미국 전략 주문이 공유한다. 라우터가 422로 변환하며 메시지는 기존 국내 문구 그대로.
    """
    allowed = {s.strip() for s in os.getenv("ALLOWED_ORDER_SOURCES", "").split(",") if s.strip()}
    if allowed and source not in allowed:
        raise ValueError(
            f"source {source!r} 는 이 계좌 broker 에서 허용되지 않음 (허용: {', '.join(sorted(allowed))})",
        )


def check_us_strategy_order(side: str, qty: int, notional_usd: float, max_usd: float) -> None:
    """미국 전략 주문 가드 — 키움 호출 전. 위반은 ValueError(라우터가 422로 변환).

    매수 1건 금액(notional_usd)이 MAX_US_ORDER_USD를 넘으면 거부. 매도는 금액 상한 없음.
    """
    if qty <= 0:
        raise ValueError(f"수량은 1 이상이어야 함: qty={qty}")
    if side not in ("buy", "sell"):
        raise ValueError(f"side는 buy/sell이어야 함: side={side!r}")
    # 매도는 금액 상한 없음 — 급등 종목의 손절/강제청산이 막히면 포지션이 갇힌다(국내 가드와 같은 이유).
    if side == "sell":
        return
    if notional_usd > max_usd:
        raise ValueError(
            f"매수 예상금액 상한 초과: {notional_usd:,.2f} > MAX_US_ORDER_USD={max_usd:,.2f}"
        )


def check_us_profile(profile: str) -> None:
    """미국 전략 주문은 US 계좌 broker(KIWOOM_PROFILE=US)에서만. 위반은 ValueError(라우터가 422로 변환).

    허용 목록이 비어 있는 broker(:8001 등)로 미국 전략 source가 와도 여기서 막는다.
    """
    if profile != "US":
        raise ValueError("US 계좌 broker(KIWOOM_PROFILE=US)에서만 미국 전략 주문")


def parse_max_us_order_usd(raw: str | None) -> float:
    """MAX_US_ORDER_USD 파싱 — 없거나 빈 값이면 5000. 숫자 아니거나 비유한·0 이하면 ValueError.

    매수일 때만 호출한다. 매도는 상한을 쓰지 않는다.
    """
    if raw is None or raw.strip() == "":
        return 5000.0
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        raise ValueError("MAX_US_ORDER_USD 설정 오류") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("MAX_US_ORDER_USD 설정 오류")
    return value
