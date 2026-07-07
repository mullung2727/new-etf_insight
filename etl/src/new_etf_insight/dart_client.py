"""DART OpenAPI 호출 모음.

프로젝트가 쓰는 DART 엔드포인트 지도 (엔드포인트마다 성격 달라 한 클라이언트로 안 묶음):
- list.json            공시목록      → fetch_all_filings (여기, daily_pipeline)
- fnlttCmpnyIndx.json  다중회사 지표  → scripts/build_financial_indicators.py
- fnlttMultiAcnt.json  다중회사 금액  → scripts/build_financial_indicators.py
- corpCode.xml(zip)    상장사 유니버스 → scripts/build_financial_indicators.py
- viewer/document      HTML·PDF       → dart_viewer.py / dart_pdf.py

공용: fetch_dart_list() — fnltt* 등 JSON 목록 API의 crtfc_key 주입 + status-000 언랩.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import requests
from dotenv import load_dotenv

from new_etf_insight.models import FilingCandidate


LIST_API_URL = "https://opendart.fss.or.kr/api/list.json"


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


def fetch_dart_list(
    endpoint: str,
    params: dict[str, Any],
    api_key: str,
    *,
    session: Any = None,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    """DART JSON 목록 엔드포인트 공용 GET.

    crtfc_key 주입 → raise_for_status → json. status 000이면 list, 그 외(013 무자료 포함)는 [].
    status 구분이 필요 없는 단순 목록 API용(fnltt* 등). status를 raise로 구분해야 하는
    호출부(list.json 페이지네이션 등)는 이 헬퍼 대신 직접 처리.
    session은 requests.Session 주입용(없으면 모듈 requests).
    """
    http = session or requests
    resp = http.get(endpoint, params={"crtfc_key": api_key, **params}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "000":
        return []
    return data.get("list") or []


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


