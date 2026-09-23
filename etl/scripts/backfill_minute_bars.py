"""전 종목 1분봉을 최근 월부터 일일 배치로 채운다.

현재 월은 매일 우선 보충하고, 그 양이 2거래일 이하이면 이어서 가장 최근의
미완료 과거 월 하나를 처리한다. 완료 상태는 minute_fetched, 반복 실패 상태는
minute_backfill_failures에 남기므로 중단 뒤 그대로 재개된다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import duckdb

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from research.watchlist_expected_return.minute_bar_store import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_SCOPE,
    MinuteFetchIncomplete,
    connect,
    fetch_into_store,
    missing_dates,
)

KRX_DB = ROOT / "etl" / "db" / "krx_ohlcv.duckdb"
FAILURE_SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_backfill_failures (
  month VARCHAR,
  ticker VARCHAR,
  scope VARCHAR,
  attempts INTEGER,
  status VARCHAR,
  last_error VARCHAR,
  last_attempt_at TIMESTAMP,
  PRIMARY KEY (month, ticker, scope)
)
"""


NXT_SUFFIX = "_NX"
NXT_TR = "ka10099"  # NXT 가능 종목 조회. broker.kiwoom.tr.EP_STKINFO로 요청
NXT_MARKETS = ("0", "10")  # KOSPI, KOSDAQ

_NXT_UNIVERSE_CACHE: set[str] | None = None


def _default_nxt_fetch(mrkt_tp: str) -> list[dict[str, Any]]:
    from broker.kiwoom import tr as kiwoom_tr
    from broker.kiwoom.client import request

    return request(NXT_TR, kiwoom_tr.EP_STKINFO, {"mrkt_tp": mrkt_tp}).data["list"]


def nxt_universe(
    fetch: Callable[[str], list[dict[str, Any]]] | None = None,
) -> set[str]:
    """NXT 거래 가능 종목 코드 집합. ka10099를 시장별(0/10)로 1회씩 조회한다.

    fetch는 테스트 주입용 (mrkt_tp) -> 행 목록. 생략 시 실제 API를 호출하고
    프로세스 실행 중 결과를 캐시한다. API 실패는 그대로 raise한다.
    """
    global _NXT_UNIVERSE_CACHE
    if fetch is not None:
        rows = [row for mrkt_tp in NXT_MARKETS for row in fetch(mrkt_tp)]
        return {str(row["code"]) for row in rows if row.get("nxtEnable") == "Y"}
    if _NXT_UNIVERSE_CACHE is None:
        _NXT_UNIVERSE_CACHE = nxt_universe(fetch=_default_nxt_fetch)
    return set(_NXT_UNIVERSE_CACHE)


def recent_months(latest_month: str, count: int) -> list[str]:
    year, month = int(latest_month[:4]), int(latest_month[4:])
    out = []
    for _ in range(count):
        out.append(f"{year:04d}{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


def load_month_plan(krx_db: Path, month: str) -> dict[str, list[str]]:
    """KRX 일봉에 실제 상장행이 있는 종목·날짜만 수집 대상으로 삼는다."""
    with duckdb.connect(str(krx_db), read_only=True) as con:
        rows = con.execute(
            "SELECT ticker, date FROM ohlcv WHERE substr(date, 1, 6)=? ORDER BY ticker, date",
            [month],
        ).fetchall()
    plan: dict[str, list[str]] = defaultdict(list)
    for ticker, date in rows:
        plan[str(ticker)].append(str(date))
    return dict(plan)


def load_blocked(con: duckdb.DuckDBPyConnection, month: str, scope: str) -> set[str]:
    con.execute(FAILURE_SCHEMA)
    return {
        row[0] for row in con.execute(
            "SELECT ticker FROM minute_backfill_failures "
            "WHERE month=? AND scope=? AND status='blocked'",
            [month, scope],
        ).fetchall()
    }


def month_progress(
    minute_db: Path, plan: dict[str, list[str]], month: str, scope: str
) -> dict[str, Any]:
    with connect(minute_db) as con:
        blocked = load_blocked(con, month, scope)
        missing_by_ticker = {
            ticker: missing_dates(con, ticker, dates, scope)
            for ticker, dates in plan.items()
        }
    missing_pairs = sum(len(dates) for dates in missing_by_ticker.values())
    actionable = {
        ticker: dates for ticker, dates in missing_by_ticker.items()
        if ticker not in blocked
    }
    return {
        "missing_by_ticker": missing_by_ticker,
        "missing_pairs": missing_pairs,
        "missing_dates": sorted({d for dates in missing_by_ticker.values() for d in dates}),
        "actionable_missing_pairs": sum(len(dates) for dates in actionable.values()),
        "actionable_missing_dates": sorted({d for dates in actionable.values() for d in dates}),
        "blocked": blocked,
    }


def record_failure(
    con: duckdb.DuckDBPyConnection,
    month: str,
    ticker: str,
    scope: str,
    error: Exception,
    max_attempts: int,
) -> str:
    con.execute(FAILURE_SCHEMA)
    previous = con.execute(
        "SELECT attempts FROM minute_backfill_failures WHERE month=? AND ticker=? AND scope=?",
        [month, ticker, scope],
    ).fetchone()
    attempts = (previous[0] if previous else 0) + 1
    status = "blocked" if attempts >= max_attempts else "retry"
    con.execute(
        """
        INSERT INTO minute_backfill_failures VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (month, ticker, scope) DO UPDATE SET
          attempts=excluded.attempts, status=excluded.status,
          last_error=excluded.last_error, last_attempt_at=excluded.last_attempt_at
        """,
        [month, ticker, scope, attempts, status, str(error)[:500]],
    )
    return status


def clear_failure(con: duckdb.DuckDBPyConnection, month: str, ticker: str, scope: str) -> None:
    con.execute(FAILURE_SCHEMA)
    con.execute(
        "DELETE FROM minute_backfill_failures WHERE month=? AND ticker=? AND scope=?",
        [month, ticker, scope],
    )


def process_month(
    minute_db: Path,
    month: str,
    plan: dict[str, list[str]],
    *,
    scope: str,
    deadline: float,
    max_attempts: int,
    max_failures: int,
    max_tickers: int | None,
    retry_blocked: bool,
    fetch_page: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    progress = month_progress(minute_db, plan, month, scope)
    attempted = completed = failed = blocked_now = bars = 0
    stop_reason = "month_complete"

    for ticker in sorted(plan):
        pending = progress["missing_by_ticker"][ticker]
        if not pending or (ticker in progress["blocked"] and not retry_blocked):
            continue
        if time.monotonic() >= deadline:
            stop_reason = "runtime_limit"
            break
        if max_tickers is not None and attempted >= max_tickers:
            stop_reason = "ticker_limit"
            break
        attempted += 1
        try:
            with connect(minute_db) as con:
                bars += fetch_into_store(
                    con, ticker, pending, scope=scope, fetch_page=fetch_page
                )
                if missing_dates(con, ticker, plan[ticker], scope):
                    raise MinuteFetchIncomplete(f"{ticker} {month}: 검증 후 미완료 날짜 존재")
                clear_failure(con, month, ticker, scope)
            completed += 1
        except Exception as exc:
            failed += 1
            if "HTTP 429" in str(exc):  # broker.kiwoom.client.KiwoomError 형식
                stop_reason = "api_rate_limit"
                break
            with connect(minute_db) as con:
                status = record_failure(con, month, ticker, scope, exc, max_attempts)
            blocked_now += status == "blocked"
            if failed >= max_failures:
                stop_reason = "failure_limit"
                break

    after = month_progress(minute_db, plan, month, scope)
    unresolved = {
        ticker: dates for ticker, dates in after["missing_by_ticker"].items()
        if dates and ticker not in after["blocked"]
    }
    if unresolved and stop_reason == "month_complete":
        stop_reason = "retry_pending"
    return {
        "month": month,
        "expected_tickers": len(plan),
        "expected_ticker_days": sum(map(len, plan.values())),
        "attempted_tickers": attempted,
        "completed_tickers": completed,
        "failed_tickers": failed,
        "newly_blocked": blocked_now,
        "blocked_total": len(after["blocked"]),
        "inserted_bars": bars,
        "remaining_ticker_days": after["missing_pairs"],
        "stop_reason": stop_reason,
    }


def run(args: argparse.Namespace, fetch_page: Callable[..., dict[str, Any]] | None = None) -> dict:
    os.environ.setdefault("KIWOOM_MIN_INTERVAL", str(args.api_interval))
    with duckdb.connect(str(args.krx_db), read_only=True) as con:
        latest_date = str(con.execute("SELECT MAX(date) FROM ohlcv").fetchone()[0])
    months = [args.month] if args.month else recent_months(latest_date[:6], args.months_back)
    deadline = time.monotonic() + args.max_runtime_min * 60

    plans = {month: load_month_plan(args.krx_db, month) for month in months}
    if args.market == "nxt":
        universe = nxt_universe()
        plans = {
            month: {f"{ticker}{NXT_SUFFIX}": dates for ticker, dates in plan.items()
                    if ticker in universe}
            for month, plan in plans.items()
        }
    selected: list[str] = []
    if args.month:
        selected = months
    else:
        current = months[0]
        current_progress = month_progress(args.minute_db, plans[current], current, args.scope)
        if current_progress["actionable_missing_pairs"]:
            selected.append(current)
        # 현재 월 신규분이 2거래일 이하일 때 과거 미완료 월 하나도 이어서 처리한다.
        if len(current_progress["actionable_missing_dates"]) <= 2:
            for month in months[1:]:
                progress = month_progress(args.minute_db, plans[month], month, args.scope)
                if progress["actionable_missing_pairs"]:
                    selected.append(month)
                    break

    if args.dry_run:
        return {
            "dry_run": True, "market": args.market,
            "latest_date": latest_date, "selected_months": selected,
            "plans": [
                {"month": month, "tickers": len(plans[month]),
                 "ticker_days": sum(map(len, plans[month].values()))}
                for month in selected
            ],
        }

    results = []
    for month in selected:
        if time.monotonic() >= deadline:
            break
        results.append(process_month(
            args.minute_db, month, plans[month], scope=args.scope, deadline=deadline,
            max_attempts=args.max_attempts, max_failures=args.max_failures,
            max_tickers=args.max_tickers, retry_blocked=args.retry_blocked,
            fetch_page=fetch_page,
        ))
        # 초기 현재 월 전체가 목표인 날에는 한 달 처리에서 종료한다.
        if month == months[0] and len(month_progress(
            args.minute_db, plans[month], month, args.scope
        )["actionable_missing_dates"]) > 2:
            break

    payload = {
        "dry_run": False,
        "market": args.market,
        "started_at": args.started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "latest_krx_date": latest_date,
        "selected_months": selected,
        "results": results,
    }
    if args.report_file:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="전 종목 1분봉 월별 역순 백필")
    parser.add_argument("--minute-db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--krx-db", type=Path, default=KRX_DB)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--market", choices=("krx", "nxt"), default="krx")
    parser.add_argument("--months-back", type=int, default=12)
    parser.add_argument("--month", help="수동 대상월 YYYYMM")
    parser.add_argument("--max-runtime-min", type=float, default=240)
    parser.add_argument("--api-interval", type=float, default=0.5)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-failures", type=int, default=20)
    parser.add_argument("--max-tickers", type=int)
    parser.add_argument("--retry-blocked", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-file", type=Path)
    args = parser.parse_args(argv)
    if args.month and (len(args.month) != 6 or not args.month.isdigit()):
        parser.error("--month는 YYYYMM 형식이어야 함")
    if args.months_back < 1 or args.max_runtime_min <= 0 or args.api_interval < 0:
        parser.error("기간·실행시간·API 간격 값을 확인할 것")
    args.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return args


def main() -> int:
    args = parse_args()
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
