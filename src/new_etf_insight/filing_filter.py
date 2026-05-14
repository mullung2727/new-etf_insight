from __future__ import annotations

from typing import Any

from new_etf_insight.models import DART_VIEW_URL, FilingCandidate


INCLUDE_REPORT_KEYWORDS = (
    "투자설명서(집합투자증권",
    "증권신고서(집합투자증권",
    "일괄신고서(집합투자증권",
    "상장지수투자신탁",
    "상장지수 집합투자기구",
)
EXCLUDE_REPORT_KEYWORDS = (
    "파생결합증권",
    "상장지수증권",
    "ETN",
    "부동산투자회사",
    "리츠",
    "REIT",
    "MMF",
    "머니마켓",
)


def candidate_reasons(filing: dict[str, Any]) -> list[str]:
    report_name = str(filing.get("report_nm", ""))
    excluded = [keyword for keyword in EXCLUDE_REPORT_KEYWORDS if keyword in report_name]
    if excluded:
        return []

    return [keyword for keyword in INCLUDE_REPORT_KEYWORDS if keyword in report_name]


def is_candidate_filing(filing: dict[str, Any]) -> bool:
    return bool(candidate_reasons(filing))


def matches_candidate_query(filing: dict[str, Any], query: str | None) -> bool:
    if not query:
        return True

    normalized_query = "".join(query.split()).lower()
    haystack = " ".join(str(filing.get(field, "")) for field in ("corp_name", "report_nm", "rcept_no"))
    normalized_haystack = "".join(haystack.split()).lower()
    return normalized_query in normalized_haystack


def to_candidate(filing: dict[str, Any]) -> FilingCandidate:
    rcept_no = str(filing.get("rcept_no", ""))

    return FilingCandidate(
        rcept_no=rcept_no,
        rcept_dt=str(filing.get("rcept_dt", "")),
        corp_name=str(filing.get("corp_name", "")),
        report_nm=str(filing.get("report_nm", "")),
        dart_url=DART_VIEW_URL.format(rcept_no=rcept_no),
        filter_reasons=candidate_reasons(filing),
    )
