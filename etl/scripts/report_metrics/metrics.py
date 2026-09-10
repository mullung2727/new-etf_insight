"""목표주가 지표 — 상승여력(R1), 직전 리포트 대비 목표가 변화율(R2).

'현재주가'는 두 개다: 리포트에 인쇄된 발간 시점 주가와 오늘 주가. 한 필드로 뭉개면
상승여력의 의미가 흐려지므로 끝까지 분리한다(PLAN §2.1).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

from .models import ReportFacts

# 인쇄된 상승여력과 계산값이 이 이상 벌어지면 파싱을 의심한다(반올림/기준일 차이 흡수용).
UPSIDE_MISMATCH_TOL = 0.01
# as_of 가 휴장일이면 직전 거래일 종가를 쓴다. 이보다 오래된 값은 '현재가'로 보지 않는다.
PRICE_LOOKBACK_DAYS = 7


def upside(target_price: Optional[int], price: Optional[int]) -> Optional[float]:
    """(목표가/주가 - 1). 주가가 없거나 0 이하면 None."""
    if target_price is None or price is None or price <= 0:
        return None
    return target_price / price - 1.0


def _krx_db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    from scripts.build_krx_ohlcv import DEFAULT_DB_PATH

    return DEFAULT_DB_PATH


def resolve_price_now(
    stock_code: str, as_of: str, db_path: str | Path | None = None
) -> Optional[int]:
    """krx_ohlcv.duckdb 에서 as_of 이하 최근 거래일 종가. 없거나 너무 오래됐으면 None."""
    path = _krx_db_path(db_path)
    if not path.exists():
        return None
    try:
        day = date.fromisoformat(as_of)
    except ValueError:
        return None
    # krx_ohlcv.date 는 'YYYYMMDD'(대시 없음) — 실측 2026-09-10
    hi = day.strftime("%Y%m%d")
    lo = (day - timedelta(days=PRICE_LOOKBACK_DAYS)).strftime("%Y%m%d")
    import duckdb

    with duckdb.connect(str(path), read_only=True) as con:
        row = con.execute(
            "SELECT close FROM ohlcv WHERE ticker = ? AND date <= ? AND date >= ? "
            "ORDER BY date DESC LIMIT 1",
            [stock_code, hi, lo],
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def report_upside(facts: ReportFacts, price_now: Optional[int] = None) -> dict[str, Any]:
    """상승여력 묶음 — 발간시점 기준/현재 기준 분리(PLAN §2.1)."""
    at_report = upside(facts.target_price, facts.price_at_report)
    mismatch = (
        facts.upside_printed is not None
        and at_report is not None
        and abs(at_report - facts.upside_printed) > UPSIDE_MISMATCH_TOL
    )
    return {
        "upside_at_report": at_report,
        "upside_now": upside(facts.target_price, price_now),
        "price_now": price_now,
        "upside_printed": facts.upside_printed,
        "upside_printed_mismatch": bool(mismatch),
    }


def target_revision(
    facts: ReportFacts, prev: Optional[Mapping[str, Any]]
) -> dict[str, Any]:
    """직전 리포트(같은 증권사) 대비 목표가 변화율.

    prev 는 storage.find_previous_report 의 결과(sqlite3.Row 또는 dict). direction 은
    변화율을 못 구한 이유까지 값으로 남긴다 — None 하나로 뭉개면 '신규 커버리지'와
    '커버리지 중단'을 구분할 수 없다.
    """
    prev_target = prev["target_price"] if prev is not None else None
    out: dict[str, Any] = {
        "prev_pdf_key": prev["pdf_key"] if prev is not None else None,
        "prev_report_date": prev["report_date"] if prev is not None else None,
        "prev_target": prev_target,
        "change_pct": None,
        "direction": "new",
        "prev_target_mismatch": bool(
            facts.prev_target_printed is not None
            and prev_target is not None
            and facts.prev_target_printed != prev_target
        ),
    }
    if prev is None:
        return out
    if prev_target is None or prev_target <= 0:
        out["direction"] = "new_target"     # 직전이 NOT RATED → 변화율 정의 안 됨
        return out
    if facts.target_price is None:
        out["direction"] = "no_target"      # 이번 리포트가 목표가를 뗐다
        return out

    change = facts.target_price / prev_target - 1.0
    out["change_pct"] = change
    out["direction"] = "flat" if change == 0 else ("up" if change > 0 else "down")
    return out
