from __future__ import annotations

from dataclasses import dataclass, field


DART_BASE_URL = "https://dart.fss.or.kr"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


@dataclass(frozen=True)
class FilingCandidate:
    rcept_no: str
    rcept_dt: str
    corp_name: str
    report_nm: str
    dart_url: str
    filter_reasons: list[str] = field(default_factory=list)
    pdf_url: str | None = None


@dataclass(frozen=True)
class EtfClassification:
    is_pre_listing_equity_etf: bool
    reasons: list[str]
