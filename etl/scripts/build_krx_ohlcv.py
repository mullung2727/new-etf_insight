"""Build/refresh the KRX all-ticker daily OHLCV cache (krx_ohlcv.duckdb).

데이터 출처: KRX OpenAPI (data-dbg.krx.co.kr, AUTH_KEY 헤더) 단일.
KRX OpenAPI는 기준일자(basDd) 단위 조회만 지원 — 기간/종목 필터 없음.

거래일 판정은 자기학습 방식:
  - 평일을 후보일로 잡고, 캐시에 없는 날만 KRX 호출.
  - 응답이 비어 있는데 그보다 나중 날짜에 데이터가 이미 있으면 휴장일 확정
    → holidays 테이블에 기록, 이후 실행에서 절대 재호출하지 않음.
  - 나중 데이터가 없는 빈 날짜(당일 미공시 가능)는 기록하지 않고 다음 실행에서 재시도.

캐시에 없는 거래일만 KRX에서 가져와 upsert 한다(자기치유 갭필).

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

ROOT = Path(__file__).resolve().parents[2]                       # new-etf_insight/
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"
ENV_PATH = ROOT / ".env"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto/"
MARKET_ENDPOINTS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
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
    market_cap    BIGINT,
    list_shrs     BIGINT,
    PRIMARY KEY (date, ticker)
)
"""

_CREATE_HOLIDAYS = """
CREATE TABLE IF NOT EXISTS holidays (
    date VARCHAR PRIMARY KEY
)
"""


def load_api_key() -> str:
    load_dotenv(ENV_PATH)
    key = os.environ.get("KRX_API_KEY")
    if not key:
        raise RuntimeError(f"KRX_API_KEY not found in {ENV_PATH}")
    return key


def get_trading_calendar(from_date: str, to_date: str) -> list[str]:
    """[from,to] 구간 거래일 후보 목록(평일, YYYYMMDD, 오름차순).

    공휴일/임시휴장은 여기서 거르지 못한다 — ensure_ohlcv 가 KRX 빈 응답으로
    휴장일을 확정해 holidays 테이블에 기록하고 이후 제외한다.
    """
    start = datetime.strptime(from_date, "%Y%m%d")
    end = datetime.strptime(to_date, "%Y%m%d")
    out: list[str] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def held_dates(con: duckdb.DuckDBPyConnection, from_date: str, to_date: str) -> set[str]:
    """캐시에 이미 1행 이상 적재된 거래일 집합."""
    rows = con.execute(
        "SELECT DISTINCT date FROM ohlcv WHERE date BETWEEN ? AND ?",
        [from_date, to_date],
    ).fetchall()
    return {r[0] for r in rows}


def holiday_dates(con: duckdb.DuckDBPyConnection, from_date: str, to_date: str) -> set[str]:
    """확정 휴장일 집합 (holidays 테이블)."""
    rows = con.execute(
        "SELECT date FROM holidays WHERE date BETWEEN ? AND ?",
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


def fetch_day(
    date: str,
    key: str,
    session: requests.Session | None = None,
    names_out: list | None = None,
) -> list[tuple]:
    """그날 KOSPI+KOSDAQ 전종목 OHLCV → 행 튜플 리스트 (거래일당 2콜).

    KRX는 인증 실패 등은 4xx로 주지만(raise_for_status가 잡음),
    방어적으로 200인데 OutBlock_1 키가 없는 응답은 에러로 처리한다
    — 빈 거래일로 위장되는 것 방지.

    names_out 리스트를 주면 같은 응답의 (code, ISU_NM) 도 append 한다
    — 추가 API콜 없이 stock_names 매핑 갱신용(ensure_ohlcv 가 사용).
    """
    http = session or requests
    rows: list[tuple] = []
    headers = {"AUTH_KEY": key}
    for market, endpoint in MARKET_ENDPOINTS.items():
        resp = http.get(
            KRX_BASE + endpoint,
            headers=headers,
            params={"basDd": date},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "OutBlock_1" not in data:
            raise RuntimeError(
                f"KRX unexpected response ({market} {date}): {resp.text[:200]}"
            )
        for x in data["OutBlock_1"]:
            if names_out is not None:
                code = str(x.get("ISU_CD", "") or "").strip()
                name = str(x.get("ISU_NM", "") or "").strip()
                if code and name:
                    names_out.append((code, name))
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
                    _parse_int(x.get("MKTCAP")),
                    _parse_int(x.get("LIST_SHRS")),
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
    확정 휴장일은 force 여부와 무관하게 재호출하지 않는다.
    날짜 단위 네트워크 오류는 해당 날짜만 건너뛰고 계속 진행(다음 실행에서 재시도).

    Returns: 통계 dict (calendar_days/held_days/missing_days/fetched_days/
             inserted_rows/holiday_days/failed_days).
    """
    con.execute(_CREATE_OHLCV)
    con.execute(_CREATE_HOLIDAYS)
    # 기존 DB 마이그레이션 — 새 컬럼 없으면 추가
    existing_cols = {r[1] for r in con.execute("PRAGMA table_info('ohlcv')").fetchall()}
    for col, dtype in [("market_cap", "BIGINT"), ("list_shrs", "BIGINT")]:
        if col not in existing_cols:
            con.execute(f"ALTER TABLE ohlcv ADD COLUMN {col} {dtype}")
    calendar = get_trading_calendar(from_date, to_date)
    held = held_dates(con, from_date, to_date)
    holidays = holiday_dates(con, from_date, to_date)
    skip = holidays if force else (held | holidays)
    missing = [d for d in calendar if d not in skip]

    session = requests.Session()
    fetched_days = 0
    inserted_rows = 0
    failed_days = 0
    empty_dates: list[str] = []
    latest_names: list[tuple[str, str]] = []   # 최신 fetch 거래일의 (code, name)
    for date in missing:
        day_names: list[tuple[str, str]] = []
        try:
            rows = fetch_day(date, key, session=session, names_out=day_names)
        except Exception as exc:
            print(f"  {date}: fetch failed - {exc} (retry next run)")
            failed_days += 1
            continue
        if not rows:
            empty_dates.append(date)
            continue
        con.executemany(
            "INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        fetched_days += 1
        inserted_rows += len(rows)
        latest_names = day_names   # missing 오름차순 → 마지막이 최신 거래일
        print(f"  {date}: {len(rows)} rows")

    # 종목명 매핑 갱신 — 최신 거래일 응답에 이미 들어온 이름 재활용(추가 API콜 0).
    # 이름은 이력 불필요 → 최신 1일만 upsert. 지연 import 로 순환참조 회피.
    if latest_names:
        try:
            from scripts.stock_names import upsert_names
        except ImportError:
            from stock_names import upsert_names
        upsert_names(con, latest_names)
        print(f"  stock_names: {len(latest_names)} names upserted")

    # 휴장일 확정: 빈 응답 날짜보다 나중 날짜에 데이터가 있으면 휴장일.
    # (마지막 빈 날짜는 당일 미공시일 수 있으므로 기록하지 않고 재시도 대상으로 남김)
    max_data_date = con.execute("SELECT max(date) FROM ohlcv").fetchone()[0]
    new_holidays = [d for d in empty_dates if max_data_date and d < max_data_date]
    if new_holidays:
        con.executemany(
            "INSERT OR IGNORE INTO holidays VALUES (?)", [(d,) for d in new_holidays]
        )
    for date in empty_dates:
        if date in new_holidays:
            print(f"  {date}: KRX empty - holiday recorded (no more retries)")
        else:
            print(f"  {date}: KRX empty - skip (retry next run)")

    return {
        "calendar_days": len(calendar),
        "held_days": len(held),
        "missing_days": len(missing),
        "fetched_days": fetched_days,
        "inserted_rows": inserted_rows,
        "holiday_days": len(new_holidays),
        "failed_days": failed_days,
    }


def _validate_date(label: str, value: str) -> None:
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise SystemExit(f"error: {label} must be YYYYMMDD, got {value!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build KRX all-ticker OHLCV cache")
    parser.add_argument("--from", dest="from_date", help="시작 거래일 YYYYMMDD")
    parser.add_argument("--to", dest="to_date", help="종료 거래일 YYYYMMDD (기본: 오늘)")
    parser.add_argument("--date", help="단일 거래일 YYYYMMDD (from=to=date)")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--force", action="store_true",
                        help="이미 적재된 거래일도 다시 받아 덮어쓰기")
    args = parser.parse_args()

    for label, value in [("--from", args.from_date), ("--to", args.to_date), ("--date", args.date)]:
        if value:
            _validate_date(label, value)

    today = datetime.now().strftime("%Y%m%d")
    if args.date:
        from_date = to_date = args.date
    else:
        to_date = args.to_date or today
        from_date = args.from_date or (datetime.now() - timedelta(days=100)).strftime("%Y%m%d")
    if from_date > to_date:
        raise SystemExit(f"error: --from {from_date} is after --to {to_date}")

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
        f"holidays+{stats['holiday_days']} failed={stats['failed_days']} "
        f"→ {args.db_path}"
    )


if __name__ == "__main__":
    main()
