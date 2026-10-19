"""전 상장사 재무지표 랭킹 (financial_indicators.sqlite3).

지표(indicators, 비율/증가율) + 금액(accounts, 매출·영업이익 등) 두 테이블에서
선택 지표 기준 상위 종목을 뽑는다. 배치: scripts/build_financial_indicators.py.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from duck_watchlist import financial_cursor
from schemas import MetricInfo, PeriodInfo, RankingRow

router = APIRouter(tags=["rankings"])

# 보고서코드 → 라벨
REPRT_LABEL = {"11011": "연간", "11013": "1분기", "11012": "반기", "11014": "3분기"}
# 같은 사업연도 안 최신순 (연간 > 3분기 > 반기 > 1분기)
REPRT_ORDER = {"11011": 4, "11014": 3, "11012": 2, "11013": 1}
# 회사 수가 최다 기간의 이 비율 미만이면 공시 진행 중으로 보고 기본에서 제외
DEFAULT_MIN_SHARE = 0.5

# 큐레이션 지표. source=indicators면 ref=idx_code, accounts면 ref=account_nm(or "op_margin" 계산).
METRICS: dict[str, dict] = {
    "roe":            {"label": "ROE",         "unit": "pct", "source": "indicators", "ref": "M211550", "order": "desc"},
    "op_margin":      {"label": "영업이익률",   "unit": "pct", "source": "accounts",   "ref": "op_margin", "order": "desc"},
    "net_margin":     {"label": "순이익률",     "unit": "pct", "source": "indicators", "ref": "M211200", "order": "desc"},
    "gross_margin":   {"label": "매출총이익률", "unit": "pct", "source": "indicators", "ref": "M211300", "order": "desc"},
    "debt_ratio":     {"label": "부채비율",     "unit": "pct", "source": "indicators", "ref": "M221100", "order": "asc"},
    "current_ratio":  {"label": "유동비율",     "unit": "pct", "source": "indicators", "ref": "M221200", "order": "desc"},
    "equity_ratio":   {"label": "자기자본비율", "unit": "pct", "source": "indicators", "ref": "M221000", "order": "desc"},
    "revenue_growth": {"label": "매출액증가율", "unit": "pct", "source": "indicators", "ref": "M231000", "order": "desc"},
    "op_growth":      {"label": "영업이익증가율", "unit": "pct", "source": "indicators", "ref": "M231400", "order": "desc"},
    "asset_turnover": {"label": "총자산회전율", "unit": "pct", "source": "indicators", "ref": "M241000", "order": "desc"},
    "revenue":        {"label": "매출액",       "unit": "won", "source": "accounts",   "ref": "매출액", "order": "desc"},
    "op_profit":      {"label": "영업이익",     "unit": "won", "source": "accounts",   "ref": "영업이익", "order": "desc"},
    "net_income":     {"label": "당기순이익",   "unit": "won", "source": "accounts",   "ref": "당기순이익(손실)", "order": "desc"},
}

# 자본잠식 = 자기자본비율 ≤ 0. (부채비율>0은 무차입 우량사 오배제 → 자기자본비율 사용)
EQUITY_RATIO_CODE = "M221000"


@router.get("/rankings/metrics", response_model=list[MetricInfo], operation_id="list_ranking_metrics")
def list_metrics() -> list[MetricInfo]:
    return [
        MetricInfo(key=k, label=m["label"], unit=m["unit"], source=m["source"], default_order=m["order"])
        for k, m in METRICS.items()
    ]


def _build_periods(rows: list[tuple[str, str, int]]) -> list[PeriodInfo]:
    """(year, reprt, 회사수) → 최신순 PeriodInfo. 기준 미달 기간은 라벨에 회사 수."""
    if not rows:
        return []
    ordered = sorted(rows, key=lambda t: (t[0], REPRT_ORDER.get(t[1], 0)), reverse=True)
    threshold = max(n for _, _, n in rows) * DEFAULT_MIN_SHARE
    default_done = False
    out: list[PeriodInfo] = []
    for y, r, n in ordered:
        label = f"{y} {REPRT_LABEL.get(r, r)}"
        if n < threshold:
            label += f" ({n:,}개사)"
            is_default = False
        else:
            is_default = not default_done
            default_done = True
        out.append(PeriodInfo(year=y, reprt=r, label=label, corp_count=n, is_default=is_default))
    return out


@router.get("/rankings/periods", response_model=list[PeriodInfo], operation_id="list_ranking_periods")
def list_periods() -> list[PeriodInfo]:
    """적재된 (year, reprt) 조합, 최신순 + 회사 수·기본 기간."""
    with financial_cursor() as con:
        rows = con.execute(
            """
            SELECT bsns_year, reprt_code, MAX(n) FROM (
                SELECT bsns_year, reprt_code, COUNT(DISTINCT corp_code) AS n FROM indicators GROUP BY 1, 2
                UNION ALL
                SELECT bsns_year, reprt_code, COUNT(DISTINCT corp_code) AS n FROM accounts GROUP BY 1, 2
            ) GROUP BY 1, 2
            """
        ).fetchall()
    return _build_periods(rows)


@router.get("/rankings", response_model=list[RankingRow], operation_id="get_rankings")
def get_rankings(
    metric: str = Query(..., description="METRICS 키 (roe, op_margin, ...)"),
    year: str = Query(..., description="사업연도 YYYY"),
    reprt: str = Query("11011", description="보고서코드 11011 연간 등"),
    exclude_impaired: bool = Query(True, description="자본잠식(자기자본비율≤0) 제외"),
    order: str | None = Query(None, description="asc|desc (기본: 지표별 default)"),
    limit: int = Query(100, ge=1, le=500),
) -> list[RankingRow]:
    m = METRICS.get(metric)
    if not m:
        raise HTTPException(404, f"unknown metric: {metric}")
    direction = "ASC" if (order or m["order"]) == "asc" else "DESC"

    impaired_clause = ""
    params: list = []
    if exclude_impaired:
        impaired_clause = (
            "AND c.corp_code IN (SELECT corp_code FROM indicators "
            "WHERE idx_code=? AND bsns_year=? AND reprt_code=? AND idx_val>0)"
        )

    if m["source"] == "indicators":
        sql = (
            "SELECT c.stock_code, c.corp_name, i.idx_val AS value "
            "FROM indicators i JOIN corps c USING(corp_code) "
            "WHERE i.idx_code=? AND i.bsns_year=? AND i.reprt_code=? AND i.idx_val IS NOT NULL "
            f"{impaired_clause} ORDER BY value {direction} LIMIT ?"
        )
        params = [m["ref"], year, reprt]
    elif m["ref"] == "op_margin":
        sql = (
            "SELECT stock_code, corp_name, op_margin AS value FROM v_key_accounts c "
            "WHERE bsns_year=? AND reprt_code=? AND op_margin IS NOT NULL "
            f"{impaired_clause} ORDER BY value {direction} LIMIT ?"
        )
        params = [year, reprt]
    else:  # accounts 금액
        sql = (
            "SELECT c.stock_code, c.corp_name, a.amount AS value "
            "FROM accounts a JOIN corps c USING(corp_code) "
            "WHERE a.account_nm=? AND a.fs_div='CFS' AND a.bsns_year=? AND a.reprt_code=? AND a.amount IS NOT NULL "
            f"{impaired_clause} ORDER BY value {direction} LIMIT ?"
        )
        params = [m["ref"], year, reprt]

    if exclude_impaired:
        params += [EQUITY_RATIO_CODE, year, reprt]
    params.append(limit)

    with financial_cursor() as con:
        rows = con.execute(sql, params).fetchall()
    return [
        RankingRow(rank=i, stock_code=sc, corp_name=nm, value=val)
        for i, (sc, nm, val) in enumerate(rows, 1)
    ]
