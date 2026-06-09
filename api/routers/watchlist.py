from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query

from duck_watchlist import krx_cursor, watchlist_cursor
from schemas import LlmScore, OhlcvCandle
from sql_utils import rows_to_dicts

router = APIRouter(tags=["watchlist"])


def _maybe_json(value: Any) -> Any:
    """sources is stored as a JSON string; decode it, else pass through."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


@router.get(
    "/watchlist",
    response_model=dict[str, list[str]],
    operation_id="list_watchlist",
    summary="Watchlist stock codes grouped by trading date",
    description=(
        "Returns the 급등(거래량 신규진입) watchlist as {YYYYMMDD: [stock_code]}, "
        "produced by build_watchlist.py. Sorted by date then code."
    ),
)
def list_watchlist() -> dict[str, list[str]]:
    with watchlist_cursor() as con:
        rows = con.execute(
            "SELECT date, stock_code FROM watchlist ORDER BY date, stock_code"
        ).fetchall()
    out: dict[str, list[str]] = {}
    for date, code in rows:
        out.setdefault(date, []).append(code)
    return out


@router.get(
    "/watchlist/scores/{date}",
    response_model=list[LlmScore],
    operation_id="get_watchlist_scores",
    summary="LLM scores for a trading date",
    description=(
        "Returns llm_scores rows for the given date (YYYYMMDD), highest score first. "
        "The frontend filters to the clicked ticker. `sources` is JSON-decoded."
    ),
)
def get_watchlist_scores(date: str) -> list[LlmScore]:
    with watchlist_cursor() as con:
        cur = con.execute(
            "SELECT * FROM llm_scores WHERE date = ? "
            "ORDER BY score DESC NULLS LAST, ticker",
            [date],
        )
        rows = rows_to_dicts(cur)
    for row in rows:
        row["sources"] = _maybe_json(row.get("sources"))
    return [LlmScore.model_validate(r) for r in rows]


@router.get(
    "/ohlcv/{ticker}",
    response_model=list[OhlcvCandle],
    operation_id="get_ohlcv",
    summary="Daily OHLCV candles for a ticker",
    description=(
        "Returns daily candles from the krx_ohlcv cache for the given 6-digit ticker, "
        "ascending by date. Optional begin/end (YYYYMMDD) bound the range; limit caps "
        "the most recent N rows (default 120)."
    ),
)
def get_ohlcv(
    ticker: str,
    begin: str | None = Query(None, description="date lower bound (YYYYMMDD)"),
    end: str | None = Query(None, description="date upper bound (YYYYMMDD)"),
    limit: int = Query(120, ge=1, le=2000, description="max most-recent rows"),
) -> list[OhlcvCandle]:
    with krx_cursor() as con:
        # Take the most-recent `limit` rows in range, then re-sort ascending.
        cur = con.execute(
            """
            SELECT date, open, high, low, close, volume, trading_value
            FROM (
                SELECT date, open, high, low, close, volume, trading_value
                FROM ohlcv
                WHERE ticker = $ticker
                  AND ($begin IS NULL OR date >= $begin)
                  AND ($end IS NULL OR date <= $end)
                ORDER BY date DESC
                LIMIT $limit
            )
            ORDER BY date
            """,
            {"ticker": ticker, "begin": begin, "end": end, "limit": limit},
        )
        rows = rows_to_dicts(cur)
    return [OhlcvCandle.model_validate(r) for r in rows]
