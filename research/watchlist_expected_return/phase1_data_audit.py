"""1단계: watchlist 기대수익 분석용 데이터 품질 감사."""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WATCHLIST_DB = ROOT / "etl" / "db" / "watchlist.sqlite3"
DEFAULT_KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"
SQLITE_TABLES = ("watchlist", "llm_scores", "intraday_ranking", "close_bet_orders")


def connect_sqlite_ro(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [table]
    ).fetchone())


def sqlite_table_summary(con: sqlite3.Connection, table: str) -> dict[str, Any]:
    if not table_exists(con, table):
        return {"exists": False, "row_count": 0, "min_date": None, "max_date": None, "columns": []}
    columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
    date_expr = "MIN(date), MAX(date)" if "date" in columns else "NULL, NULL"
    row = con.execute(f'SELECT COUNT(*), {date_expr} FROM "{table}"').fetchone()
    return {
        "exists": True,
        "row_count": row[0],
        "min_date": row[1],
        "max_date": row[2],
        "columns": columns,
    }


def load_sqlite_keys(con: sqlite3.Connection, table: str, ticker_column: str) -> set[tuple[str, str]]:
    if not table_exists(con, table):
        return set()
    return {
        (str(row[0]), str(row[1]))
        for row in con.execute(f'SELECT date, "{ticker_column}" FROM "{table}"')
    }


def load_ohlcv(
    con: duckdb.DuckDBPyConnection,
    dates: list[str],
    tickers: list[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    if not dates or not tickers:
        return {}
    date_marks = ",".join("?" for _ in dates)
    ticker_marks = ",".join("?" for _ in tickers)
    rows = con.execute(
        f"""
        SELECT date, ticker, open, high, low, close, volume, trading_value, market_cap
        FROM ohlcv
        WHERE date IN ({date_marks}) AND ticker IN ({ticker_marks})
        """,
        [*dates, *tickers],
    ).fetchall()
    return {
        (str(row[0]), str(row[1])): {
            "open": row[2], "high": row[3], "low": row[4], "close": row[5],
            "volume": row[6], "trading_value": row[7], "market_cap": row[8],
        }
        for row in rows
    }


def _rate(value: int, total: int) -> float | None:
    return round(value / total, 4) if total else None


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "mean": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 6),
        "median": round(statistics.median(values), 6),
        "mean": round(statistics.fmean(values), 6),
        "max": round(max(values), 6),
    }


def audit_databases(watchlist_db: Path, krx_db: Path) -> dict[str, Any]:
    with closing(connect_sqlite_ro(watchlist_db)) as sql:
        tables = {table: sqlite_table_summary(sql, table) for table in SQLITE_TABLES}
        watchlist = sorted(load_sqlite_keys(sql, "watchlist", "stock_code"))
        scores = load_sqlite_keys(sql, "llm_scores", "ticker")
        ranking = load_sqlite_keys(sql, "intraday_ranking", "ticker")
        orders = load_sqlite_keys(sql, "close_bet_orders", "ticker")
        order_rows = list(sql.execute("SELECT * FROM close_bet_orders")) if table_exists(sql, "close_bet_orders") else []

    if not krx_db.exists():
        raise FileNotFoundError(krx_db)
    with duckdb.connect(str(krx_db), read_only=True) as krx:
        ohlcv_columns = [row[1] for row in krx.execute("PRAGMA table_info('ohlcv')").fetchall()]
        krx_count, krx_min, krx_max = krx.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM ohlcv"
        ).fetchone()
        trading_dates = [str(row[0]) for row in krx.execute(
            "SELECT DISTINCT date FROM ohlcv ORDER BY date"
        ).fetchall()]
        date_index = {date: index for index, date in enumerate(trading_dates)}
        required_dates: set[str] = set()
        for date, _ticker in watchlist:
            required_dates.add(date)
            if date in date_index:
                start = date_index[date] + 1
                required_dates.update(trading_dates[start:start + 5])
        ohlcv = load_ohlcv(
            krx,
            sorted(required_dates),
            sorted({ticker for _date, ticker in watchlist}),
        )

    total = len(watchlist)
    entry_covered = sum(
        (date, ticker) in ohlcv and ohlcv[(date, ticker)]["close"] is not None
        for date, ticker in watchlist
    )
    horizon_eligible: dict[int, int] = defaultdict(int)
    horizon_covered: dict[int, int] = defaultdict(int)
    scored_horizon_covered: dict[int, int] = defaultdict(int)
    for date, ticker in watchlist:
        index = date_index.get(date)
        if index is None:
            continue
        for horizon in range(1, 6):
            future_index = index + horizon
            if future_index >= len(trading_dates):
                continue
            horizon_eligible[horizon] += 1
            future_date = trading_dates[future_index]
            row = ohlcv.get((future_date, ticker))
            if row and all(row[field] is not None for field in ("open", "high", "low", "close")):
                horizon_covered[horizon] += 1
                if (date, ticker) in scores and ohlcv.get((date, ticker), {}).get("close") is not None:
                    scored_horizon_covered[horizon] += 1

    slippage: list[float] = []
    net_pnl_pct: list[float] = []
    sell_cost_rate: list[float] = []
    order_columns = set(tables["close_bet_orders"]["columns"])
    for row in order_rows:
        values = dict(row)
        entry = ohlcv.get((str(values.get("date")), str(values.get("ticker"))))
        close = entry.get("close") if entry else None
        fill = values.get("cntr_price")
        if close and fill:
            slippage.append(fill / close - 1)
        if "pnl_pct" in order_columns and values.get("pnl_pct") is not None:
            net_pnl_pct.append(float(values["pnl_pct"]) / 100)
        qty = values.get("sell_qty") or values.get("cntr_qty") or values.get("qty")
        sell_price = values.get("sell_price")
        costs = (values.get("sell_cmsn") or 0) + (values.get("sell_tax") or 0)
        if qty and sell_price and costs:
            sell_cost_rate.append(costs / (qty * sell_price))

    warnings: list[str] = []
    score_columns = set(tables["llm_scores"]["columns"])
    if "generated_at" not in score_columns:
        warnings.append("llm_scores에 점수 생성시각이 없어 D일 장 마감 전 생성 여부를 DB만으로 검증할 수 없음")
    if not ({"raw", "evidence_timestamp"} & score_columns):
        warnings.append("llm_scores에 원문과 근거시각이 없어 텍스트 특징의 미래정보 누출 여부를 검증할 수 없음")
    if horizon_eligible[1] < total:
        warnings.append("최신 watchlist 일부는 아직 D+1 거래일 결과가 없어 성과 분석 대상에서 검열됨")
    if entry_covered < total:
        warnings.append("일부 watchlist 종목은 D일 KRX 종가가 없어 진입 기준가를 계산할 수 없음")

    return {
        "audit_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "inputs": {"watchlist_db": str(watchlist_db.resolve()), "krx_db": str(krx_db.resolve())},
        "sqlite_tables": tables,
        "krx_ohlcv": {
            "row_count": krx_count,
            "min_date": krx_min,
            "max_date": krx_max,
            "columns": ohlcv_columns,
            "trading_date_count": len(trading_dates),
        },
        "join_coverage": {
            "watchlist_rows": total,
            "score_rows_joined": len(set(watchlist) & scores),
            "score_join_rate": _rate(len(set(watchlist) & scores), total),
            "intraday_rows_joined": len(set(watchlist) & ranking),
            "intraday_join_rate": _rate(len(set(watchlist) & ranking), total),
            "order_rows_joined": len(set(watchlist) & orders),
            "order_join_rate": _rate(len(set(watchlist) & orders), total),
            "entry_close_covered": entry_covered,
            "entry_close_rate": _rate(entry_covered, total),
            "phase2_primary_cohort": scored_horizon_covered[1],
            "forward_ohlcv": {
                f"d_plus_{horizon}": {
                    "eligible": horizon_eligible[horizon],
                    "covered": horizon_covered[horizon],
                    "coverage_rate": _rate(horizon_covered[horizon], horizon_eligible[horizon]),
                }
                for horizon in range(1, 6)
            },
        },
        "actual_orders": {
            "close_to_fill_slippage": _distribution(slippage),
            "stored_net_pnl_rate": _distribution(net_pnl_pct),
            "stored_sell_cost_rate": _distribution(sell_cost_rate),
            "note": "매수 수수료 컬럼이 없어 저장된 매도비용만 별도 산출; pnl_pct는 존재 시 broker 순손익률 사용",
        },
        "temporal_integrity": {
            "score_generated_at_available": "generated_at" in score_columns,
            "evidence_timestamp_available": bool({"raw", "evidence_timestamp"} & score_columns),
        },
        "warnings": warnings,
    }


def render_markdown(audit: dict[str, Any]) -> str:
    coverage = audit["join_coverage"]
    lines = [
        "# Watchlist 기대수익 연구 — 1단계 데이터 감사",
        "",
        f"- 생성시각: {audit['generated_at']}",
        f"- watchlist 표본: {coverage['watchlist_rows']}건",
        f"- 기존 점수 연결: {coverage['score_rows_joined']}건 ({coverage['score_join_rate']})",
        f"- D일 종가 연결: {coverage['entry_close_covered']}건 ({coverage['entry_close_rate']})",
        f"- 실제 주문 연결: {coverage['order_rows_joined']}건 ({coverage['order_join_rate']})",
        f"- 2단계 주 분석 표본: {coverage['phase2_primary_cohort']}건",
        "",
        "## 미래 OHLCV 연결",
        "",
        "| 기간 | 분석 가능 | OHLCV 완전 | 연결률 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for horizon, values in coverage["forward_ohlcv"].items():
        lines.append(f"| {horizon} | {values['eligible']} | {values['covered']} | {values['coverage_rate']} |")
    lines.extend(["", "## 실제 주문 분포", ""])
    for label, key in [
        ("종가 대비 매수 체결가 차이", "close_to_fill_slippage"),
        ("저장된 순손익률", "stored_net_pnl_rate"),
        ("저장된 매도비용률", "stored_sell_cost_rate"),
    ]:
        value = audit["actual_orders"][key]
        lines.append(f"- {label}: n={value['count']}, 평균={value['mean']}, 중앙값={value['median']}")
    lines.extend(["", "## 한계", ""])
    lines.extend(f"- {warning}" for warning in audit["warnings"])
    return "\n".join(lines) + "\n"


def write_results(audit: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "phase1_data_audit.json"
    md_path = output_dir / "phase1_data_audit.md"
    json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(audit), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="watchlist 기대수익 연구 1단계 데이터 감사")
    parser.add_argument("--watchlist-db", type=Path, default=DEFAULT_WATCHLIST_DB)
    parser.add_argument("--krx-db", type=Path, default=DEFAULT_KRX_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    audit = audit_databases(args.watchlist_db, args.krx_db)
    json_path, md_path = write_results(audit, args.output_dir)
    print(f"[phase1] {json_path}")
    print(f"[phase1] {md_path}")


if __name__ == "__main__":
    main()
