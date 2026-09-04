"""신규상장 종목의 자동매매 진입을 막는 공통 후보 가드."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

MIN_LISTING_AGE_DAYS = 30


def is_listing_age_allowed(
    first_trade_date: str | None,
    as_of_date: str,
    min_age_days: int = MIN_LISTING_AGE_DAYS,
) -> bool:
    """최초 거래일부터 달력일 기준 min_age_days 이상 지난 종목만 허용한다."""
    if not first_trade_date:
        return False
    first = datetime.strptime(first_trade_date, "%Y%m%d").date()
    as_of = datetime.strptime(as_of_date, "%Y%m%d").date()
    return (as_of - first).days >= min_age_days


def load_first_trade_dates(con, tickers: Iterable[str], as_of_date: str) -> dict[str, str]:
    """KRX OHLCV에서 기준일 이하 최초 거래일을 조회한다."""
    unique = list(dict.fromkeys(tickers))
    if not unique:
        return {}
    placeholders = ",".join("?" for _ in unique)
    rows = con.execute(
        f"SELECT ticker, MIN(date) FROM ohlcv WHERE date<=? AND ticker IN ({placeholders}) GROUP BY ticker",
        [as_of_date, *unique],
    ).fetchall()
    return {ticker: first_date for ticker, first_date in rows}
