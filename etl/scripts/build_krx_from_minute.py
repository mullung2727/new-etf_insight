"""전날 일봉을 새벽 분봉(09:00~15:30)으로 먼저 적재 (KRX 도착 전 선행).

매일 07:00 실행. KRX 가 들어오면 build_krx_ohlcv.ensure_ohlcv 가
reconcile_day 로 대조·교정한다. 분봉 출처 날짜는 ohlcv_provisional 에 기록
(ohlcv 스키마 변경 없음).

Usage (from etl/):
    uv run python scripts/build_krx_from_minute.py
    uv run python scripts/build_krx_from_minute.py --today 20261104
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401

import argparse
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd

from new_etf_insight.data_paths import db_dir
from build_krx_ohlcv import _CREATE_OHLCV, _insert_ohlcv_rows, ensure_provisional_table
from check_krx_trading_day import is_krx_trading_day
import market_session

KST = timezone(timedelta(hours=9))


def prev_trading_day(today: str) -> str:
    """today 직전 거래일 (최대 15일 역행)."""
    day = datetime.strptime(today, "%Y%m%d")
    for _ in range(15):
        day -= timedelta(days=1)
        key = day.strftime("%Y%m%d")
        if is_krx_trading_day(key):
            return key
    raise RuntimeError(f"no KRX trading day in 15 days before {today}")


def aggregate_day(mcon: duckdb.DuckDBPyConnection, date: str) -> pd.DataFrame:
    """그날 정규장 분봉 → 종목별 OHLC·합산량 (시간외 제외).

    minute_fetched 에 완료 표시된 (ticker, scope, date)만 집계한다.
    status 칸이 없는 옛 DB에선 JOIN만 하고 status 조건은 뺀다.
    정규장 범위·종가 봉은 market_session.session(date) (수능·연초 반영).
    """
    op, cl = market_session.session(date)
    t_open, t_close = op.replace(":", "") + "00", cl.replace(":", "") + "00"
    cols = {r[1] for r in mcon.execute("PRAGMA table_info('minute_fetched')").fetchall()}
    status_filter = " AND f.status = 'complete'" if "status" in cols else ""
    return mcon.execute(
        f"""
        SELECT b.ticker AS ticker,
            arg_min(b.open, b.time) AS open,
            max(b.high) AS high,
            min(b.low) AS low,
            arg_max(b.close, b.time) AS close,
            sum(b.volume) AS volume,
            sum(b.close * b.volume) AS trading_value,
            bool_or(b.time = ?) AS has1530
        FROM minute_bars AS b
        INNER JOIN minute_fetched AS f
          ON f.ticker = b.ticker AND f.scope = b.scope AND f.date = b.date
        WHERE b.scope = '1' AND b.date = ? AND b.time BETWEEN ? AND ?{status_filter}
        GROUP BY b.ticker
        """,
        [t_close, date, t_open, t_close],
    ).fetchdf()


def _num(value):
    """pandas 스칼라 → int/None (NaN 가드)."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    return int(value)


def build_rows(
    kcon: duckdb.DuckDBPyConnection, agg: pd.DataFrame, date: str
) -> tuple[list[tuple], int]:
    """분봉 집계 + 직전 KRX 일봉(P) → ohlcv 행. ±30% 밖은 제외하고 센다."""
    has_prov = (
        kcon.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name = 'ohlcv_provisional'"
        ).fetchone()[0]
        > 0
    )
    prov_filter = " AND date NOT IN (SELECT date FROM ohlcv_provisional)" if has_prov else ""
    prev_date = kcon.execute(
        f"SELECT max(date) FROM ohlcv WHERE date < ?{prov_filter}", [date]
    ).fetchone()[0]
    if prev_date is None or len(agg) == 0:
        return [], 0
    prev = {
        r[0]: (r[1], r[2], r[3])
        for r in kcon.execute(
            "SELECT ticker, market, close, list_shrs FROM ohlcv WHERE date = ?",
            [prev_date],
        ).fetchall()
    }
    rows: list[tuple] = []
    skipped_corp = 0
    for rec in agg.to_dict(orient="records"):
        if rec["ticker"] not in prev:
            continue
        market_p, close_p, shrs_p = prev[rec["ticker"]]
        close = _num(rec.get("close"))
        if close_p is None or close_p == 0 or close is None:
            continue
        if abs(close / close_p - 1) > 0.30:
            skipped_corp += 1
            continue
        rows.append(
            (
                date,
                rec["ticker"],
                market_p,
                _num(rec.get("open")),
                _num(rec.get("high")),
                _num(rec.get("low")),
                close,
                _num(rec.get("volume")),
                _num(rec.get("trading_value")),
                close * shrs_p if shrs_p is not None else None,
                shrs_p,
                close - close_p,
            )
        )
    return rows, skipped_corp


def run(
    today: str,
    kcon: duckdb.DuckDBPyConnection,
    mcon: duckdb.DuckDBPyConnection,
) -> dict:
    """D=직전 거래일 분봉 일봉 적재. exists/no_minute/loaded 중 하나 반환."""
    target = prev_trading_day(today)
    kcon.execute(_CREATE_OHLCV)
    ensure_provisional_table(kcon)
    held = kcon.execute(
        "SELECT COUNT(*) FROM ohlcv WHERE date = ?", [target]
    ).fetchone()[0]
    if held > 0:
        return {"status": "exists", "date": target}
    agg = aggregate_day(mcon, target)
    if len(agg) == 0:
        return {"status": "no_minute", "date": target}
    rows, skipped_corp = build_rows(kcon, agg, target)
    if len(rows) == 0:
        return {"status": "no_minute", "date": target}
    now = datetime.now(KST).isoformat(timespec="seconds")
    no1530 = [t for t, h in zip(agg["ticker"], agg["has1530"]) if not h]
    kcon.execute("BEGIN")
    try:
        _insert_ohlcv_rows(kcon, rows)
        kcon.execute(
            "INSERT OR REPLACE INTO ohlcv_provisional VALUES (?, ?, ?, ?)",
            [target, len(rows), skipped_corp, now],
        )
        kcon.execute("DELETE FROM ohlcv_provisional_no1530 WHERE date = ?", [target])
        if no1530:
            kcon.executemany(
                "INSERT INTO ohlcv_provisional_no1530 VALUES (?, ?)",
                [(target, t) for t in no1530],
            )
        kcon.execute("COMMIT")
    except Exception:
        kcon.execute("ROLLBACK")
        raise
    return {
        "status": "loaded",
        "date": target,
        "rows": len(rows),
        "skipped_corp": skipped_corp,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="분봉으로 전날 일봉 선적재")
    parser.add_argument("--today", help="기준일 YYYYMMDD (기본: KST 오늘)")
    args = parser.parse_args(argv)
    if args.today:
        try:
            datetime.strptime(args.today, "%Y%m%d")
        except ValueError:
            raise SystemExit(f"error: --today must be YYYYMMDD, got {args.today!r}")
        today = args.today
    else:
        today = datetime.now(KST).strftime("%Y%m%d")
    target = prev_trading_day(today)
    try:
        mcon = duckdb.connect(str(db_dir() / "minute_bars.duckdb"), read_only=True)
    except Exception:
        print(f"MINUTE_DAILY status=minute_locked date={target}")
        return 1
    try:
        krx_path = db_dir() / "krx_ohlcv.duckdb"
        krx_path.parent.mkdir(parents=True, exist_ok=True)
        kcon = duckdb.connect(str(krx_path))
        try:
            res = run(today, kcon, mcon)
        finally:
            kcon.close()
    finally:
        mcon.close()
    if res["status"] == "loaded":
        print(
            f"MINUTE_DAILY status=loaded date={res['date']} "
            f"rows={res['rows']} skipped_corp={res['skipped_corp']}"
        )
        return 0
    print(f"MINUTE_DAILY status={res['status']} date={res['date']}")
    return 0 if res["status"] == "exists" else 1


if __name__ == "__main__":
    raise SystemExit(main())
