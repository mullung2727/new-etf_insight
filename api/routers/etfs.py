from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from duck import get_cursor
from schemas import EtfDetail, EtfListItem, Holding
from sql_utils import parse_json_array, rows_to_dicts

router = APIRouter(tags=["etfs"])


def _etf_record_columns() -> set[str]:
    cur = get_cursor()
    cur.execute("PRAGMA table_info('etf_records')")
    return {row[1] for row in cur.fetchall()}


@router.get(
    "/etfs",
    response_model=list[EtfListItem],
    operation_id="list_etfs",
    summary="List ETF filings",
    description=(
        "Returns ETF filings filtered by date range, country, theme classification "
        "and pre-listing flag. Mirrors the legacy web/lib/queries.ts getEtfList."
    ),
)
def list_etfs(
    begin: str | None = Query(None, description="first_rcept_dt lower bound (YYYYMMDD)"),
    end: str | None = Query(None, description="first_rcept_dt upper bound (YYYYMMDD)"),
    country: str | None = Query(None, description="primary_country exact match"),
    theme_status: str | None = Query(None),
    theme_bucket: str | None = Query(None),
    pre_listing_only: bool | None = Query(None),
) -> list[EtfListItem]:
    columns = _etf_record_columns()
    has_theme = "theme_status" in columns and "theme_bucket" in columns
    theme_select = (
        "theme_status, theme_bucket, structure_tags, classification_confidence,"
        if has_theme
        else "NULL AS theme_status, NULL AS theme_bucket, '[]' AS structure_tags, NULL AS classification_confidence,"
    )
    theme_where = (
        "AND ($theme_status IS NULL OR theme_status = $theme_status) "
        "AND ($theme_bucket IS NULL OR theme_bucket = $theme_bucket)"
        if has_theme
        else ""
    )
    params: dict[str, object] = {
        "begin": begin,
        "end": end,
        "country": country,
        "pre_listing": pre_listing_only,
    }
    if has_theme:
        params["theme_status"] = theme_status or None
        params["theme_bucket"] = theme_bucket or None

    sql = f"""
        SELECT etf_key, fund_name, asset_manager, index_name,
               primary_country, {theme_select} first_rcept_dt, is_pre_listing_etf, revision_count,
               EXISTS(SELECT 1 FROM etf_holdings h WHERE h.etf_key = r.etf_key) AS has_holdings
        FROM etf_records r
        WHERE ($begin IS NULL OR first_rcept_dt >= $begin)
          AND first_rcept_dt <= COALESCE($end, STRFTIME('%Y%m%d', CURRENT_DATE))
          AND ($country IS NULL OR primary_country = $country)
          {theme_where}
          AND ($pre_listing IS NULL OR is_pre_listing_etf = $pre_listing)
        ORDER BY first_rcept_dt DESC
    """
    cur = get_cursor()
    cur.execute(sql, params)
    rows = rows_to_dicts(cur)
    for row in rows:
        row["structure_tags"] = parse_json_array(row.get("structure_tags"))
    return [EtfListItem.model_validate(r) for r in rows]


@router.get(
    "/etfs/{etf_key}",
    response_model=EtfDetail,
    operation_id="get_etf_detail",
    summary="Get ETF detail",
    description="Returns the full etf_records row for the given etf_key.",
)
def get_etf_detail(etf_key: str) -> EtfDetail:
    cur = get_cursor()
    cur.execute("SELECT * FROM etf_records WHERE etf_key = $etf_key", {"etf_key": etf_key})
    rows = rows_to_dicts(cur)
    if not rows:
        raise HTTPException(status_code=404, detail=f"etf_key not found: {etf_key}")
    row = rows[0]
    row["keywords"] = parse_json_array(row.get("keywords"))
    row["structure_tags"] = parse_json_array(row.get("structure_tags"))
    row["missing_info"] = parse_json_array(row.get("missing_info"))
    return EtfDetail.model_validate(row)


@router.get(
    "/etfs/{etf_key}/holdings",
    response_model=list[Holding],
    operation_id="get_etf_holdings",
    summary="Get ETF holdings",
    description="Returns the holdings list (ordered by seq) for the given etf_key.",
)
def get_etf_holdings(etf_key: str) -> list[Holding]:
    cur = get_cursor()
    cur.execute(
        "SELECT name, ticker, exchange, weight FROM etf_holdings WHERE etf_key = $etf_key ORDER BY seq",
        {"etf_key": etf_key},
    )
    rows = rows_to_dicts(cur)
    return [Holding.model_validate(r) for r in rows]


@router.get(
    "/countries",
    response_model=list[str],
    operation_id="list_countries",
    summary="List distinct primary_country values",
)
def list_countries() -> list[str]:
    cur = get_cursor()
    cur.execute(
        "SELECT DISTINCT primary_country FROM etf_records "
        "WHERE primary_country IS NOT NULL ORDER BY primary_country"
    )
    return [row[0] for row in cur.fetchall()]
