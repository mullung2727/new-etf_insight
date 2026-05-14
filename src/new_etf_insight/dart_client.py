from __future__ import annotations

import os
import subprocess
from datetime import date, timedelta
from typing import Any

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from new_etf_insight.models import DART_BASE_URL, FilingCandidate
from new_etf_insight.pdf_finder import extract_dcm_no, extract_pdf_links, pick_primary_pdf_link


LIST_API_URL = "https://opendart.fss.or.kr/api/list.json"


def request_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    return session


def get_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("DART_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env에 DART_API_KEY가 없어")
    return api_key


def recent_date_range(days: int) -> tuple[str, str]:
    if days < 1:
        raise ValueError("days는 1 이상이어야 해")
    end = date.today()
    begin = end - timedelta(days=days - 1)
    return begin.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_filing_page(api_key: str, begin: str, end: str, page_no: int, page_count: int) -> dict[str, Any]:
    response = requests.get(
        LIST_API_URL,
        params={
            "crtfc_key": api_key,
            "bgn_de": begin,
            "end_de": end,
            "page_no": page_no,
            "page_count": page_count,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_all_filings(
    api_key: str,
    begin: str,
    end: str,
    page_count: int = 100,
    max_pages: int = 50,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filings: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}

    for page_no in range(1, max_pages + 1):
        payload = fetch_filing_page(api_key, begin, end, page_no, page_count)
        last_payload = payload
        status = payload.get("status")
        if status == "013":
            break
        if status != "000":
            raise RuntimeError(f"DART API 오류: status={status}, message={payload.get('message')}")

        page_filings = payload.get("list") or []
        filings.extend(page_filings)
        total_count = int(payload.get("total_count") or 0)
        if not page_filings or len(filings) >= total_count:
            break

    return filings, last_payload


def fetch_text(url: str) -> str:
    try:
        response = request_session().get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException:
        result = subprocess.run(
            ["curl", "-fsSL", "-A", "Mozilla/5.0", url],
            check=True,
            capture_output=True,
        )
        return result.stdout.decode("utf-8", errors="replace")


def find_pdf_url(candidate: FilingCandidate) -> str | None:
    page_html = fetch_text(candidate.dart_url)
    dcm_no = extract_dcm_no(page_html)
    if not dcm_no:
        return None

    download_page_url = f"{DART_BASE_URL}/pdf/download/main.do?rcp_no={candidate.rcept_no}&dcm_no={dcm_no}"
    download_html = fetch_text(download_page_url)
    return pick_primary_pdf_link(extract_pdf_links(download_html, DART_BASE_URL))
