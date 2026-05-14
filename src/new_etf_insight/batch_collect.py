from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from new_etf_insight.dart_client import fetch_all_filings, find_pdf_url, get_api_key
from new_etf_insight.etf_classifier import classify_pre_listing_equity_etf
from new_etf_insight.filing_filter import is_candidate_filing, matches_candidate_query, to_candidate
from new_etf_insight.pdf_text import download_pdf, extract_pdf_text
from new_etf_insight.sample_summary import summarize_hyundai_physical_ai
from new_etf_insight.storage import append_jsonl


def collect_candidates(
    begin: str,
    end: str,
    page_count: int = 100,
    max_pages: int = 50,
    query: str | None = None,
) -> list[dict[str, Any]]:
    api_key = get_api_key()
    filings, _payload = fetch_all_filings(api_key, begin, end, page_count, max_pages)

    candidates = []
    for filing in filings:
        if not is_candidate_filing(filing) or not matches_candidate_query(filing, query):
            continue

        candidate = to_candidate(filing)
        candidate_dict = asdict(candidate)
        try:
            pdf_url = find_pdf_url(candidate)
            candidate_dict = asdict(replace(candidate, pdf_url=pdf_url))
        except Exception as error:  # noqa: BLE001 - 후보별 실패를 남기고 다음 후보로 진행
            candidate_dict["pdf_error"] = str(error)
        candidates.append(candidate_dict)

    return candidates


def summarize_pdf_text(text: str, source: dict[str, Any]) -> dict[str, Any]:
    classification = classify_pre_listing_equity_etf(text)
    if not classification.is_pre_listing_equity_etf:
        return {
            "rcept_no": source.get("rcept_no"),
            "is_pre_listing_equity_etf": False,
            "classification_reasons": classification.reasons,
            "source": source,
        }
    return summarize_hyundai_physical_ai(text, source)


def summarize_pdf_url(pdf_url: str, source: dict[str, Any]) -> dict[str, Any]:
    return summarize_pdf_text(extract_pdf_text(download_pdf(pdf_url)), source)


def summarize_pdf_file(path: Path, source: dict[str, Any]) -> dict[str, Any]:
    return summarize_pdf_text(extract_pdf_text(path.read_bytes()), source)


def collect_daily_summaries(
    begin: str,
    end: str,
    output_path: Path,
    page_count: int = 100,
    max_pages: int = 50,
    query: str | None = None,
) -> list[dict[str, Any]]:
    summaries = []
    for candidate in collect_candidates(begin, end, page_count, max_pages, query):
        pdf_url = candidate.get("pdf_url")
        if not pdf_url:
            summaries.append(
                {
                    "rcept_no": candidate.get("rcept_no"),
                    "is_pre_listing_equity_etf": False,
                    "failure_reason": "PDF 링크 없음",
                    "source": candidate,
                }
            )
            continue

        try:
            summaries.append(summarize_pdf_url(str(pdf_url), candidate))
        except Exception as error:  # noqa: BLE001 - 실패 후보도 JSONL에 남기는 수집 단계
            summaries.append(
                {
                    "rcept_no": candidate.get("rcept_no"),
                    "is_pre_listing_equity_etf": False,
                    "failure_reason": str(error),
                    "source": candidate,
                }
            )

    append_jsonl(output_path, summaries)
    return summaries
