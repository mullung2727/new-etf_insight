from __future__ import annotations

from dataclasses import dataclass


DART_BASE_URL = "https://dart.fss.or.kr"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


@dataclass(frozen=True)
class FilingCandidate:
    rcept_no: str
    rcept_dt: str
    corp_code: str
    corp_name: str
    report_nm: str
    dart_url: str


@dataclass(frozen=True)
class EtfClassification:
    is_pre_listing_equity_etf: bool
    reasons: list[str]
