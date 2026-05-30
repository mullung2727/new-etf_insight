from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from kiwoom import conditions
from kiwoom.models import ConditionItem

router = APIRouter(prefix="/conditions", tags=["conditions"])


@router.get(
    "",
    operation_id="list_conditions",
    summary="조건검색식 목록 조회",
    response_model=list[ConditionItem],
)
def list_conditions() -> list[ConditionItem]:
    """HTS에 저장된 조건검색식 목록(seq, name)을 반환한다."""
    return conditions.list_conditions()


@router.get(
    "/{seq}/run",
    operation_id="run_condition",
    summary="조건검색 단발 실행",
)
def run_condition(seq: str) -> Any:
    """조건검색식을 단발 실행해 조건 충족 종목을 반환한다."""
    return conditions.run_condition(seq)
