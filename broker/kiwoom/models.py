"""Pydantic models for tool inputs/outputs.

Output models stay loose (``extra="ignore"``) and carry a ``raw`` passthrough
because Kiwoom field names vary by TR/version. Input models enforce the
contract the MCP tools expose to the model.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Side(str, Enum):
    buy = "buy"
    sell = "sell"


class OrderType(str, Enum):
    limit = "limit"     # 지정가 (price required)
    market = "market"   # 시장가 (price ignored)


class OrderRequest(_Base):
    symbol: str = Field(description="종목코드 6자리, 예: 005930")
    side: Side
    qty: int = Field(gt=0, description="주문 수량 (주)")
    price: int = Field(ge=0, default=0, description="지정가 가격(원). 시장가면 0")
    order_type: OrderType = OrderType.limit
    source: str = Field(
        default="manual",
        description="주문 출처 — 거래 원장 기록용. ETL 배치='close_bet', 수동 주문='manual'",
    )


class Quote(_Base):
    symbol: str
    price: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OrderResult(_Base):
    accepted: bool
    order_no: str | None = None
    message: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class ConditionItem(_Base):
    seq: str
    name: str
