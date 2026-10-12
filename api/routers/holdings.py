from __future__ import annotations

from fastapi import APIRouter, Query

from duck import read_cursor
from schemas import DailyHolding, HoldingEvent, HoldingPoint
from sql_utils import rows_to_dicts

router = APIRouter(tags=["holdings"])


def _flags(row: dict) -> dict:
    w = row.get("weight_pct") or 0.0
    row["is_conviction"] = w >= 5.0
    row["is_faded"] = w <= 3.0
    return row


def _base_weights(cur, etf: str, where_sql: str, latest: str) -> dict | None:
    cur.execute(
        f"SELECT MAX(asof) FROM etf_holdings_daily WHERE etf = ? AND {where_sql}",
        [etf, latest],
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return None
    cur.execute(
        "SELECT code, weight_pct FROM etf_holdings_daily WHERE etf = ? AND asof = ?",
        [etf, row[0]],
    )
    return {r[0]: r[1] or 0.0 for r in cur.fetchall()}


@router.get(
    "/holdings/latest",
    response_model=list[DailyHolding],
    operation_id="get_latest_holdings",
    summary="Latest holdings snapshot for a TIME ETF",
    description=(
        "Returns the most recent etf_holdings_daily rows for the given ETF code "
        "(426030/385720/495060/456600), highest weight first. is_conviction "
        "(w>=5) and is_faded (w<=3) flags drive the UI highlight. d1/d7/d30 are weight changes (%p) vs the previous, 7-day-ago (or nearest earlier), and 30-day-ago (or nearest earlier) snapshots; None when no earlier snapshot exists."
    ),
)
def get_latest_holdings(
    etf: str = Query(..., description="ETF code"),
) -> list[DailyHolding]:
    with read_cursor() as cur:
        cur.execute("SELECT MAX(asof) FROM etf_holdings_daily WHERE etf = ?", [etf])
        row = cur.fetchone()
        if not row or not row[0]:
            return []
        latest = row[0]
        base_d1 = _base_weights(cur, etf, "asof < ?", latest)
        base_d7 = _base_weights(cur, etf, "asof <= date(?, '-7 days')", latest)
        base_d30 = _base_weights(cur, etf, "asof <= date(?, '-30 days')", latest)
        cur.execute(
            "SELECT asof, etf, code, name, qty, amt_krw, weight_pct "
            "FROM etf_holdings_daily WHERE etf = ? AND asof = ? ORDER BY weight_pct DESC",
            [etf, latest],
        )
        rows = rows_to_dicts(cur)
    for r in rows:
        w = r.get("weight_pct") or 0.0
        rcode = r.get("code")
        r["d1"] = round(w - base_d1.get(rcode, 0.0), 2) if base_d1 is not None else None
        r["d7"] = round(w - base_d7.get(rcode, 0.0), 2) if base_d7 is not None else None
        r["d30"] = round(w - base_d30.get(rcode, 0.0), 2) if base_d30 is not None else None
    return [DailyHolding.model_validate(_flags(r)) for r in rows]


@router.get(
    "/holdings/events",
    response_model=list[HoldingEvent],
    operation_id="list_holding_events",
    summary="Holding change events (entries/exits/crossings)",
    description=(
        "Returns etf_holding_events rows, newest first. kind is ENTRY (new), "
        "EXIT (removed), UP5 (crossed above 5%) or DN3 (fell below 3%)."
    ),
)
def list_holding_events(
    etf: str | None = Query(None, description="ETF code filter"),
    days: int = Query(30, ge=1, le=365, description="lookback window in days"),
    limit: int = Query(200, ge=1, le=2000, description="max rows"),
) -> list[HoldingEvent]:
    with read_cursor() as cur:
        cur.execute(
            "SELECT date, etf, code, name, kind, w_prev, w_cur FROM etf_holding_events "
            "WHERE (? IS NULL OR etf = ?) AND date >= date('now', 'localtime', ?) "
            "ORDER BY date DESC, etf, kind LIMIT ?",
            [etf, etf, f"-{days} days", limit],
        )
        rows = rows_to_dicts(cur)
    return [HoldingEvent.model_validate(r) for r in rows]


@router.get(
    "/holdings/history",
    response_model=list[HoldingPoint],
    operation_id="get_holding_history",
    summary="Weight history for one holding",
    description="Returns (asof, weight_pct) ascending for the given ETF + code.",
)
def get_holding_history(
    etf: str = Query(..., description="ETF code"),
    code: str = Query(..., description="holding code"),
) -> list[HoldingPoint]:
    with read_cursor() as cur:
        cur.execute(
            "SELECT asof, weight_pct FROM etf_holdings_daily "
            "WHERE etf = ? AND code = ? ORDER BY asof",
            [etf, code],
        )
        rows = rows_to_dicts(cur)
    return [HoldingPoint.model_validate(r) for r in rows]
