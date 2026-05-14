from __future__ import annotations

import argparse
import os
from datetime import date, timedelta
from typing import Any

import requests
from dotenv import load_dotenv


API_URL = "https://opendart.fss.or.kr/api/list.json"
DART_VIEW_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcp_no}"

ETF_KEYWORDS = ("ETF", "상장지수집합투자기구", "집합투자증권")
DISCLOSURE_KEYWORDS = ("신규상장", "증권신고서", "투자설명서")
EXCLUDE_KEYWORDS = ("파생결합증권", "상장지수증권", "ETN")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="최근 DART 공시를 조회하고 ETF 신규상장 후보를 느슨하게 표시한다."
    )
    parser.add_argument("--days", type=int, default=7, help="조회할 최근 일수")
    parser.add_argument("--begin", help="조회 시작일(YYYYMMDD). --end와 함께 사용")
    parser.add_argument("--end", help="조회 종료일(YYYYMMDD). --begin과 함께 사용")
    parser.add_argument("--page-count", type=int, default=100, help="가져올 공시 수")
    parser.add_argument("--max-pages", type=int, default=50, help="최대 조회 페이지 수")
    parser.add_argument("--query", action="append", default=[], help="응답 필드에서 찾을 검색어. 여러 번 지정 가능")
    return parser.parse_args()


def get_api_key() -> str:
    load_dotenv()
    api_key = os.getenv("DART_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(".env에 DART_API_KEY가 없어")
    return api_key


def recent_date_range(days: int) -> tuple[str, str]:
    if days < 1:
        raise ValueError("--days는 1 이상이어야 해")

    end = date.today()
    begin = end - timedelta(days=days - 1)
    return begin.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def resolve_date_range(days: int, begin: str | None, end: str | None) -> tuple[str, str]:
    if begin or end:
        if not begin or not end:
            raise ValueError("--begin과 --end는 같이 써야 해")
        return begin, end
    return recent_date_range(days)


def fetch_filings(api_key: str, begin: str, end: str, page_count: int, page_no: int) -> dict[str, Any]:
    response = requests.get(
        API_URL,
        params={
            "crtfc_key": api_key,
            "bgn_de": begin,
            "end_de": end,
            "page_no": page_no,
            "page_count": page_count,
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def fetch_all_filings(
    api_key: str,
    begin: str,
    end: str,
    page_count: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    filings: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}

    for page_no in range(1, max_pages + 1):
        payload = fetch_filings(api_key, begin, end, page_count, page_no)
        last_payload = payload

        status = payload.get("status")
        if status == "013":
            break
        if status != "000":
            raise RuntimeError(f"DART API 오류: status={status}, message={payload.get('message')}")

        page_filings = payload.get("list") or []
        filings.extend(page_filings)

        total_count = int(payload.get("total_count") or 0)
        if len(filings) >= total_count or not page_filings:
            break

    return filings, last_payload


def matches_query(filing: dict[str, Any], query: str) -> bool:
    normalized_query = "".join(query.split()).lower()
    if not normalized_query:
        return False

    haystack = " ".join(
        str(filing.get(field, ""))
        for field in ("corp_name", "report_nm", "flr_nm", "rcept_no")
    )
    normalized_haystack = "".join(haystack.split()).lower()
    return normalized_query in normalized_haystack


def candidate_reason(filing: dict[str, Any]) -> str | None:
    report_name = str(filing.get("report_nm", ""))
    excluded = [keyword for keyword in EXCLUDE_KEYWORDS if keyword in report_name]
    if excluded:
        return None

    matched_etf = [keyword for keyword in ETF_KEYWORDS if keyword in report_name]
    matched_disclosure = [keyword for keyword in DISCLOSURE_KEYWORDS if keyword in report_name]

    if matched_etf and matched_disclosure:
        return f"ETF 키워드={','.join(matched_etf)} / 공시 키워드={','.join(matched_disclosure)}"
    return None


def print_filing(filing: dict[str, Any], index: int, reason: str | None = None) -> None:
    rcp_no = str(filing.get("rcept_no", ""))
    print(f"{index}. {filing.get('rcept_dt')} | {filing.get('corp_name')} | {filing.get('report_nm')}")
    print(f"   접수번호: {rcp_no}")
    print(f"   원문: {DART_VIEW_URL.format(rcp_no=rcp_no)}")
    if reason:
        print(f"   후보 이유: {reason}")


def main() -> None:
    args = parse_args()
    api_key = get_api_key()
    begin, end = resolve_date_range(args.days, args.begin, args.end)

    filings, payload = fetch_all_filings(api_key, begin, end, args.page_count, args.max_pages)
    status = payload.get("status")
    message = payload.get("message")
    total_count = payload.get("total_count")

    print(f"DART 공시검색 호출 완료: {begin}~{end}")
    print(f"status={status}, message={message}, total_count={total_count}, fetched_count={len(filings)}")

    if not filings:
        return

    for query in args.query:
        matched = [filing for filing in filings if matches_query(filing, query)]
        print(f"\n검색어 매칭: {query} -> {len(matched)}건")
        for index, filing in enumerate(matched[:20], start=1):
            print_filing(filing, index)

    candidates = [
        (filing, reason)
        for filing in filings
        if (reason := candidate_reason(filing)) is not None
    ]

    print(f"\nETF 신규상장 후보(초안 필터): {len(candidates)}건")
    for index, (filing, reason) in enumerate(candidates[:20], start=1):
        print_filing(filing, index, reason)

    print("\n최근 공시 샘플:")
    for index, filing in enumerate(filings[:10], start=1):
        print_filing(filing, index)


if __name__ == "__main__":
    main()
