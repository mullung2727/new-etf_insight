from __future__ import annotations

from dataclasses import asdict
from typing import Any

from new_etf_insight.dart_client import fetch_all_filings, get_api_key
from new_etf_insight.filing_filter import is_candidate_filing, matches_candidate_query, to_candidate


def collect_candidates(
    begin: str,
    end: str,
    page_count: int = 100,
    max_pages: int = 50,
    query: str | None = None,
) -> list[dict[str, Any]]:
    api_key = get_api_key()
    filings, _payload = fetch_all_filings(api_key, begin, end, page_count, max_pages)

    return [
        asdict(to_candidate(filing))
        for filing in filings
        if is_candidate_filing(filing) and matches_candidate_query(filing, query)
    ]
