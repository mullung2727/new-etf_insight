"""Pydantic response models.

Field names and types mirror the TypeScript interfaces in
``web/lib/queries.ts`` exactly. Do not rename a field here without updating
the TS side as well — the BFF relies on a 1:1 JSON shape.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EtfListItem(_Base):
    etf_key: str
    fund_name: str | None = None
    asset_manager: str | None = None
    index_name: str | None = None
    primary_country: str | None = None
    theme_status: str | None = None
    theme_bucket: str | None = None
    structure_tags: list[str] | None = None
    classification_confidence: float | None = None
    first_rcept_dt: str | None = None
    is_pre_listing_etf: bool | None = None
    revision_count: int | None = None
    has_holdings: bool = False


class EtfDetail(EtfListItem):
    route: str | None = None
    index_provider: str | None = None
    index_description: str | None = None
    holdings_available_in_pdf: bool | None = None
    holdings_summary: str | None = None
    classification_evidence: str | None = None
    keywords: list[str] | None = None
    trend_summary: str | None = None
    missing_info: list[str] | None = None
    rcept_no: str | None = None
    rcept_dt: str | None = None
    corp_code: str | None = None
    corp_name: str | None = None
    report_nm: str | None = None
    fund_code: str | None = None
    pdf_path: str | None = None


class Holding(_Base):
    name: str
    ticker: str | None = None
    exchange: str | None = None
    weight: str | None = None


class HoldingStat(_Base):
    name: str
    avg_weight: float
    etf_count: int


class StatsSummary(_Base):
    total_etfs: int
    with_any_holdings: int
