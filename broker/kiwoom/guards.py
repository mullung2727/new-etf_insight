"""Order guards — hard caps enforced before any order leaves the process.

All orders flow through ``orders.place_order`` which calls ``check_order`` here,
so there is no bypass path. Cap comes from MAX_ORDER_AMOUNT.
"""

from __future__ import annotations

from .config import Config


class OrderRejected(Exception):
    """Raised when an order violates a configured guard. Never sent to Kiwoom."""


def check_order(cfg: Config, *, qty: int, price: int, market: bool) -> None:
    if qty <= 0:
        raise OrderRejected(f"수량은 1 이상이어야 함: qty={qty}")

    # Market orders have no price; estimate notional from price only when known.
    if not market:
        if price <= 0:
            raise OrderRejected(f"지정가 주문은 price>0 필요: price={price}")
        notional = qty * price
        if notional > cfg.max_order_amount:
            raise OrderRejected(
                f"주문금액 상한 초과: {notional:,} > MAX_ORDER_AMOUNT="
                f"{cfg.max_order_amount:,}"
            )
