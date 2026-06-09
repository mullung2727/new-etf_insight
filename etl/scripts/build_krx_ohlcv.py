"""Build/refresh the KRX all-ticker daily OHLCV cache (krx_ohlcv.duckdb).

데이터 출처가 둘로 나뉜다:
  - 거래일 달력: pykrx (네이버, 기준종목 005930 일봉 인덱스)
  - OHLCV 본체  : KRX OpenAPI (data-dbg.krx.co.kr, AUTH_KEY 헤더)

캐시에 없는 거래일만 KRX에서 가져와 upsert 한다(자기치유 갭필).
휴장일/주말은 달력에 애초에 없으므로 절대 재호출하지 않는다(무한루프 방지).

Usage (from etl/):
    uv run python scripts/build_krx_ohlcv.py                       # 최근 ~100일 → 오늘
    uv run python scripts/build_krx_ohlcv.py --from 20250101 --to 20250131
    uv run python scripts/build_krx_ohlcv.py --date 20250102       # 단일 거래일
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import duckdb
import requests
from dotenv import load_dotenv
from pykrx import stock

ROOT = Path(__file__).resolve().parents[2]                       # new-etf_insight/
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"
ENV_PATH = ROOT / ".env"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto/"
MARKET_ENDPOINTS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
CALENDAR_TICKER = "005930"        # 삼성전자 — 거래일 달력 기준종목
REQUEST_SLEEP = 0.1               # KRX rate-limit 완충 (콜 사이)
REQUEST_TIMEOUT = 30

_CREATE_OHLCV = """
CREATE TABLE IF NOT EXISTS ohlcv (
    date          VARCHAR,
    ticker        VARCHAR,
    market        VARCHAR,
    open          INTEGER,
    high          INTEGER,
    low           INTEGER,
    close         INTEGER,
    volume        BIGINT,
    trading_value BIGINT,
    PRIMARY KEY (date, ticker)
)
"""


def load_api_key() -> str:
    load_dotenv(ENV_PATH)
    key = os.environ.get("KRX_API_KEY")
    if not key:
        raise RuntimeError(f"KRX_API_KEY not found in {ENV_PATH}")
    return key


def get_trading_calendar(from_date: str, to_date: str) -> list[str]:
    """[from,to] 구간 실거래일 목록(YYYYMMDD, 오름차순).

    기준종목 005930 일봉 인덱스를 쓴다. KRX는 거래일에만 행을 주므로
    휴장일/주말은 자연히 빠진다. (컬럼명은 로케일에 따라 깨질 수 있으나
    인덱스(날짜)만 사용하므로 무관.)
    """
    df = stock.get_market_ohlcv(from_date, to_date, CALENDAR_TICKER)
    return [d.strftime("%Y%m%d") for d in df.index]


def held_dates(con: duckdb.DuckDBPyConnection, from_date: str, to_date: str) -> set[str]:
    """캐시에 이미 1행 이상 적재된 거래일 집합."""
    rows = con.execute(
        "SELECT DISTINCT date FROM ohlcv WHERE date BETWEEN ? AND ?",
        [from_date, to_date],
    ).fetchall()
    return {r[0] for r in rows}


def _parse_int(value: str | None) -> int | None:
    """KRX 문자열 숫자 → int. 콤마 제거, '-'/'' → None(거래정지 등)."""
    if value is None:
        return None
    s = value.replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def fetch_day(date: str, key: str) -> list[tuple]:
    """그날 KOSPI+KOSDAQ 전종목 OHLCV → 행 튜플 리스트 (거래일당 2콜)."""
    rows: list[tuple] = []
    headers = {"AUTH_KEY": key}
    for market, endpoint in MARKET_ENDPOINTS.items():
        resp = requests.get(
            KRX_BASE + endpoint,
            headers=headers,
            params={"basDd": date},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        for x in resp.json().get("OutBlock_1", []):
            rows.append(
                (
                    x["BAS_DD"],
                    x["ISU_CD"],          # 6자리 단축코드 (키움과 동일)
                    market,
                    _parse_int(x.get("TDD_OPNPRC")),
                    _parse_int(x.get("TDD_HGPRC")),
                    _parse_int(x.get("TDD_LWPRC")),
                    _parse_int(x.get("TDD_CLSPRC")),
                    _parse_int(x.get("ACC_TRDVOL")),
                    _parse_int(x.get("ACC_TRDVAL")),
                )
            )
        time.sleep(REQUEST_SLEEP)
    return rows


def ensure_ohlcv(
    con: duckdb.DuckDBPyConnection,
    from_date: str,
    to_date: str,
    key: str,
    force: bool = False,
) -> dict:
    """[from,to] 구간 누락 거래일만 KRX에서 채워 upsert. STEP2 배치가 재사용.

    force=True 면 이미 적재된 거래일도 다시 받아 덮어쓴다(INSERT OR REPLACE).

    Returns: 통계 dict (calendar_days/held_days/missing_days/fetched_days/inserted_rows).
    """
    con.execute(_CREATE_OHLCV)
    calendar = get_trading_calendar(from_date, to_date)
    held = held_dates(con, from_date, to_date)
    missing = list(calendar) if force else [d for d in calendar if d not in held]

    fetched_days = 0
    inserted_rows = 0
    for date in missing:
        rows = fetch_day(date, key)
        if not rows:
            print(f"  {date}: KRX empty - skip (retry next run)")
            continue
        con.executemany(
            "INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?)", rows
        )
        fetched_days += 1
        inserted_rows += len(rows)
        print(f"  {date}: {len(rows)} rows")

    return {
        "calendar_days": len(calendar),
        "held_days": len(held),
        "missing_days": len(missing),
        "fetched_days": fetched_days,
        "inserted_rows": inserted_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build KRX all-ticker OHLCV cache")
    parser.add_argument("--from", dest="from_date", help="시작 거래일 YYYYMMDD")
    parser.add_argument("--to", dest="to_date", help="종료 거래일 YYYYMMDD (기본: 오늘)")
    parser.add_argument("--date", help="단일 거래일 YYYYMMDD (from=to=date)")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--force", action="store_true",
                        help="이미 적재된 거래일도 다시 받아 덮어쓰기")
    args = parser.parse_args()

    today = datetime.now().strftime("%Y%m%d")
    if args.date:
        from_date = to_date = args.date
    else:
        to_date = args.to_date or today
        from_date = args.from_date or (datetime.now() - timedelta(days=100)).strftime("%Y%m%d")

    key = load_api_key()
    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(args.db_path))
    try:
        stats = ensure_ohlcv(con, from_date, to_date, key, force=args.force)
    finally:
        con.close()

    print(
        f"[{from_date}~{to_date}] calendar={stats['calendar_days']} "
        f"held={stats['held_days']} missing={stats['missing_days']} "
        f"fetched={stats['fetched_days']}d/{stats['inserted_rows']}rows "
        f"→ {args.db_path}"
    )


if __name__ == "__main__":
    main()
