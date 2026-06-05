from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from kiwoom import account

router = APIRouter(prefix="/account", tags=["account"])


@router.get(
    "/balance",
    operation_id="get_balance",
    summary="계좌 보유종목/평가 잔고 조회",
)
def get_balance() -> Any:
    """계좌평가잔고내역 전 페이지를 병합해 반환한다."""
    return account.get_balance()


@router.get(
    "/deposit",
    operation_id="get_deposit",
    summary="예수금 상세 조회 (kt00001)",
)
def get_deposit() -> Any:
    """주문 가능 현금(예수금) 상세를 반환한다."""
    return account.get_deposit()
