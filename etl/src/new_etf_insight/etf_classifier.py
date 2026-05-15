from __future__ import annotations

from new_etf_insight.models import EtfClassification


ETF_MARKERS = ("상장지수투자신탁", "상장지수 집합투자기구", "상장지수집합투자기구", "ETF")
EQUITY_MARKERS = ("증권(주식형)", "국내주식에 60%이상", "국내주식 60%이상", "투자신탁재산의 60%이상을 국내주식")
PRE_LISTING_MARKERS = ("상장일: 2026년 00월 00일(예정)", "한국거래소 상장(예정)", "상장(예정)")


def classify_pre_listing_equity_etf(text: str) -> EtfClassification:
    reasons = []
    if any(marker in text for marker in ETF_MARKERS):
        reasons.append("상장지수")
    if any(marker in text for marker in EQUITY_MARKERS):
        reasons.append("주식형")
    if any(marker in text for marker in PRE_LISTING_MARKERS):
        reasons.append("상장예정")

    return EtfClassification(
        is_pre_listing_equity_etf={"상장지수", "주식형", "상장예정"}.issubset(set(reasons)),
        reasons=reasons,
    )
