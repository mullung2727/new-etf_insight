"""report_metrics 자료구조 — PLAN §2.4 스키마와 1:1.

ReportFacts 의 필드 순서/이름은 report_facts 테이블 컬럼과 같아야 한다(Stage 0 테스트가 강제).
새 컬럼을 추가할 땐 storage._SCHEMA 와 여기를 같이 고친다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

PARSER_VERSION = "1"

# parse_status 값 — 조용한 실패를 막으려고 실패 사유를 값으로 남긴다(PLAN §0).
STATUS_OK = "ok"
STATUS_NO_TARGET = "no_target"
STATUS_IMAGE_PDF = "unsupported_image_pdf"
STATUS_PARSE_ERROR = "parse_error"


@dataclass
class ReportFacts:
    pdf_key: str
    pdf_path: str
    stock_code: str
    stock_name: Optional[str]
    broker: str
    report_date: str                       # YYYY-MM-DD
    opinion: Optional[str] = None
    target_price: Optional[int] = None
    price_at_report: Optional[int] = None
    price_at_report_date: Optional[str] = None
    upside_printed: Optional[float] = None
    prev_target_printed: Optional[int] = None
    parse_status: str = STATUS_OK
    parse_error: Optional[str] = None
    parser_version: str = PARSER_VERSION
    parsed_at: Optional[str] = None        # upsert 시점에 storage 가 채운다


@dataclass
class YearEstimate:
    """리포트 추정표 한 연도. 금액 단위는 전부 억원으로 정규화(PLAN §2.4)."""

    fiscal_year: int
    is_forecast: bool
    revenue: Optional[float] = None
    operating_profit: Optional[float] = None
    net_profit: Optional[float] = None
    eps: Optional[float] = None
    per: Optional[float] = None
    roe: Optional[float] = None
    pbr: Optional[float] = None
    div_yield: Optional[float] = None


@dataclass
class RimInputs:
    """RIM 입력. B 는 자기자본(억원), roe 는 %."""

    base_equity: float                     # B0 = base_year 말 자기자본
    base_year: int = 0
    years: list[int] = field(default_factory=list)
    roes: list[float] = field(default_factory=list)      # %
    equities: list[float] = field(default_factory=list)  # 각 연도 말 B_t
    r: float = 0.08
    omega: float = 0.8
    warnings: list[str] = field(default_factory=list)
