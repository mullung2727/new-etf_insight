from __future__ import annotations

from fastapi import APIRouter, Query

from duck import read_cursor
from schemas import HoldingStat, StatsSummary
from sql_utils import rows_to_dicts

router = APIRouter(tags=["stats"], prefix="/stats")


def _etf_key_params(etf_keys: list[str]) -> tuple[str, dict[str, object]]:
    placeholders = ", ".join(f"$k{i}" for i in range(len(etf_keys)))
    return placeholders, {f"k{i}": k for i, k in enumerate(etf_keys)}


@router.get(
    "/holdings",
    response_model=list[HoldingStat],
    operation_id="holdings_stats",
    summary="Top 20 holdings by average weight",
    description=(
        "Aggregates holdings across ETFs and returns the top 20 by avg_weight. "
        "When etf_keys is provided, restricts to that subset (mirrors getHoldingsStatsByKeys); "
        "otherwise filters by pre-listing flag plus date/country (mirrors getHoldingsStats)."
    ),
)
def holdings_stats(
    begin: str | None = Query(None),
    end: str | None = Query(None),
    country: str | None = Query(None),
    etf_keys: list[str] | None = Query(None, description="Restrict aggregate to these etf_keys"),
) -> list[HoldingStat]:
    with read_cursor() as cur:
        if etf_keys:
            placeholders, params = _etf_key_params(etf_keys)
            params["total_etfs"] = len(etf_keys)
            cur.execute(
                f"""
                SELECT
                  h.name,
                  ROUND(SUM(raw) / $total_etfs, 2) AS avg_weight,
                  CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS etf_count
                FROM (
                  SELECT etf_key, name,
                         TRY_CAST(REPLACE(REPLACE(weight, '%', ''), ' ', '') AS DOUBLE) AS raw
                  FROM etf_holdings
                  WHERE etf_key IN ({placeholders})
                    AND weight IS NOT NULL
                ) h
                WHERE h.raw BETWEEN 0 AND 100
                GROUP BY h.name
                ORDER BY avg_weight DESC
                LIMIT 20
                """,
                params,
            )
        else:
            cur.execute(
                """
                SELECT
                  h.name,
                  ROUND(AVG(TRY_CAST(REPLACE(REPLACE(h.weight, '%', ''), ' ', '') AS DOUBLE)), 2) AS avg_weight,
                  CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS etf_count
                FROM etf_holdings h
                JOIN etf_records r ON h.etf_key = r.etf_key
                WHERE r.is_pre_listing_etf = true
                  AND ($begin IS NULL OR r.first_rcept_dt >= $begin)
                  AND r.first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
                  AND ($country IS NULL OR r.primary_country = $country)
                  AND h.weight IS NOT NULL
                GROUP BY h.name
                ORDER BY avg_weight DESC
                LIMIT 20
                """,
                {"begin": begin, "end": end, "country": country},
            )
        rows = rows_to_dicts(cur)
    return [HoldingStat.model_validate(r) for r in rows]


@router.get(
    "/summary",
    response_model=StatsSummary,
    operation_id="stats_summary",
    summary="Counts of ETFs and ETFs with holdings",
    description=(
        "Returns total_etfs and with_any_holdings. When etf_keys is provided, "
        "restricts to that subset (mirrors getStatsSummaryByKeys); "
        "otherwise filters by pre-listing flag plus date/country (mirrors getStatsSummary)."
    ),
)
def stats_summary(
    begin: str | None = Query(None),
    end: str | None = Query(None),
    country: str | None = Query(None),
    etf_keys: list[str] | None = Query(None),
) -> StatsSummary:
    with read_cursor() as cur:
        if etf_keys:
            placeholders, params = _etf_key_params(etf_keys)
            cur.execute(
                f"""
                SELECT
                  CAST(COUNT(DISTINCT r.etf_key) AS INTEGER) AS total_etfs,
                  CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS with_any_holdings
                FROM etf_records r
                LEFT JOIN etf_holdings h ON r.etf_key = h.etf_key
                WHERE r.etf_key IN ({placeholders})
                """,
                params,
            )
        else:
            cur.execute(
                """
                SELECT
                  CAST(COUNT(DISTINCT r.etf_key) AS INTEGER) AS total_etfs,
                  CAST(COUNT(DISTINCT h.etf_key) AS INTEGER) AS with_any_holdings
                FROM etf_records r
                LEFT JOIN etf_holdings h ON r.etf_key = h.etf_key
                WHERE r.is_pre_listing_etf = true
                  AND ($begin IS NULL OR r.first_rcept_dt >= $begin)
                  AND r.first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
                  AND ($country IS NULL OR r.primary_country = $country)
                """,
                {"begin": begin, "end": end, "country": country},
            )
        rows = rows_to_dicts(cur)
    return StatsSummary.model_validate(rows[0]) if rows else StatsSummary(total_etfs=0, with_any_holdings=0)
