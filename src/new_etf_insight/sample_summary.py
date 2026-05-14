from __future__ import annotations

import re
from typing import Any

from new_etf_insight.etf_classifier import classify_pre_listing_equity_etf


PORTFOLIO_NAMES = (
    "현대차",
    "기아",
    "현대모비스",
    "레인보우로보틱스",
    "현대오토에버",
    "LG이노텍",
    "LG씨엔에스",
    "두산로보틱스",
    "로보티즈",
    "에스엘",
    "HL만도",
    "고영",
    "휴림로봇",
    "클로봇",
    "하이젠알앤엠",
)


def _normalize_date(match: re.Match[str] | None) -> str | None:
    if not match:
        return None
    year, month, day = match.groups()
    if month == "00" or day == "00":
        return None
    return f"{year}-{month}-{day}"


def _first(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _portfolio_example(text: str) -> list[dict[str, float | str]]:
    portfolio = []
    for name in PORTFOLIO_NAMES:
        match = re.search(rf"{re.escape(name)}\s+(\d+\.\d+)", text)
        if match:
            portfolio.append({"name": name, "weight": float(match.group(1))})
    return portfolio


def _clean_manager(manager: str | None) -> str | None:
    if manager is None:
        return None
    return re.sub(r"\(.*?\)", "", manager).strip()


def summarize_hyundai_physical_ai(text: str, source: dict[str, Any]) -> dict[str, Any]:
    classification = classify_pre_listing_equity_etf(text)
    etf_name = _first(r"집합투자기구 명칭:\s*(KB RISE 현대차고정피지컬AI 증권 상장지수 투자신탁\(주식\))", text)
    fund_code = _first(r"\((ET\d+)\)", text)
    manager = _clean_manager(_first(r"집합투자업자 명칭:\s*([^\n]+)", text))
    effective_date = _normalize_date(re.search(r"효력발생일:\s*(\d{4})년\s*(\d{2})월\s*(\d{2})일", text))
    expected_listing_date = _normalize_date(re.search(r"상장일:\s*(\d{4})년\s*(\d{2})월\s*(\d{2})일", text))
    underlying_index = _first(r"[“'\u2018](KEDI 현대차고정피지컬AI 지수\(시장가격\))[”'\u2019]", text)
    total_fee = _first(r"투자신탁보수 합계\s+(\d+\.\d+)", text)

    return {
        "rcept_no": source.get("rcept_no"),
        "etf_name": etf_name,
        "manager": manager,
        "fund_code": fund_code,
        "effective_date": effective_date,
        "expected_listing_date": expected_listing_date,
        "underlying_index": underlying_index,
        "strategy": "기초지수 구성종목과 구성비율을 완전 복제하는 방식",
        "total_fee": float(total_fee) if total_fee is not None else None,
        "portfolio_example_as_of": "2026-03-31" if "2026년 03월 31일 종가 기준" in text else None,
        "portfolio_example": _portfolio_example(text),
        "keywords": ["현대차", "피지컬AI", "로봇", "자동차"],
        "theme": "피지컬AI",
        "is_pre_listing_equity_etf": classification.is_pre_listing_equity_etf,
        "classification_reasons": classification.reasons,
        "source": source,
    }
