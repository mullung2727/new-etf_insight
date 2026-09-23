"""DB 배선 — 0값 제외·주간행렬·유니버스 필터.

가드 출처: research/BACKTEST_DATA.md §(a)(b)(d).
ETF/ETN은 ohlcv에 없어 필터 불필요. 관리종목은 데이터 소스 없어 TODO.
"""
from __future__ import annotations

import math

from .weekly import daily_returns, week_key, weekly_returns

DAILY_SQL = """
WITH mkt AS (SELECT DISTINCT date FROM ohlcv WHERE date BETWEEN ? AND ?),
m AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS ms FROM mkt)
SELECT o.ticker, o.date, m.ms, o.close, o.volume, o.list_shrs,
       COALESCE(o.trading_value, o.close * o.volume) AS turnover, o.open AS open_price,
       o.high AS high_price, o.low AS low_price
FROM ohlcv o JOIN m USING (date)
LEFT JOIN stock_names sn ON sn.code = o.ticker
WHERE o.date BETWEEN ? AND ?
  AND o.volume > 0 AND o.open > 0 AND o.close > 0
  AND right(o.ticker, 1) = '0'
  AND (sn.name IS NULL
       OR (sn.name NOT LIKE '%스팩%' AND sn.name NOT LIKE '%기업인수목적%'))
ORDER BY o.ticker, o.date
"""


def load_daily(con, start: str, end: str) -> list[dict]:
    """0값 행 제외 + 시장순번(ms) 포함 일봉."""
    cols = ("ticker", "date", "ms", "close", "volume", "list_shrs",
            "turnover", "open_price", "high_price", "low_price")
    return [dict(zip(cols, row))
            for row in con.execute(DAILY_SQL, [start, end, start, end]).fetchall()]


def weekly_series(rows: list[dict]) -> dict[str, dict[str, float]]:
    """티커별 주간수익률 시리즈. rows는 날짜순 정렬해 사용한다."""
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    return {t: weekly_returns(daily_returns(sorted(rs, key=lambda r: r["date"])))
            for t, rs in by_ticker.items()}


def matrix_from_series(series: dict[str, dict[str, float]], weeks: int = 52,
                       end: str | None = None
                       ) -> tuple[list[str], dict[str, list[float]]]:
    """미리 계산한 시리즈에서 주키 그리드/행렬만 잘라낸다."""
    keys = sorted({k for s in series.values() for k in s})
    if end is not None:
        keys = [k for k in keys if k <= week_key(end)]
    grid = keys[-weeks:]
    required = math.ceil(len(grid) * 0.8)
    mat = {t: [s.get(k, 0.0) for k in grid]
           for t, s in series.items()
           if sum(1 for k in grid if k in s) >= required}
    return grid, mat


def build_weekly_matrix(rows: list[dict], weeks: int = 52,
                        end: str | None = None) -> tuple[list[str], dict[str, list[float]]]:
    """{ticker: 52주 수익률} + 주키 그리드. 결측주는 0.0 채움.

    커버리지 하한 = ceil(그리드*0.8). 52주 그리드면 42주.
    """
    return matrix_from_series(weekly_series(rows), weeks=weeks, end=end)
