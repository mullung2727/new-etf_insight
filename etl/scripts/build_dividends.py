"""전 상장사 DART 배당에 관한 사항(alotMatter) 적재 배치.

DART alotMatter.json 응답 행을 sqlite(db/financial_indicators.sqlite3)의
dividends 테이블에 긴 형식(항목 se × 보통주/우선주 × 사업연도)으로 저장한다.
한 응답 행의 thstrm/frmtrm/lwfr(당기/전기/전전기)를 fiscal_year 3행으로 펼친다.

alotMatter는 corp_code 단일 필수(시장 전체 스캔 없음). 유니버스는 같은 DB의
기존 corps 테이블.

Usage (from etl/):
    uv run python scripts/build_dividends.py --year 2025 --reprt 11011 --skip-existing
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path: etl/·scripts/·src/)

import requests  # noqa: E402

from new_etf_insight.dart_client import DartAPIError, fetch_dart_list, get_api_key  # noqa: E402
from wl_sqlite import connect_rw  # noqa: E402
from new_etf_insight.data_paths import db_dir  # noqa: E402
from build_financial_indicators import _to_float  # noqa: E402

ALOT_API_URL = "https://opendart.fss.or.kr/api/alotMatter.json"

DEFAULT_DB_PATH = db_dir() / "financial_indicators.sqlite3"

DELAY = 0.1  # sec / 호출
REQUEST_TIMEOUT = 30

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(con) -> None:
    """dividends 테이블 + 랭킹 인덱스. commit은 호출자가 유지한다."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS dividends (
            corp_code   TEXT NOT NULL,
            stock_code  TEXT,
            fiscal_year TEXT NOT NULL,
            se          TEXT NOT NULL,
            stock_knd   TEXT NOT NULL DEFAULT '',
            value       REAL,
            value_raw   TEXT,
            src_year    TEXT NOT NULL,
            reprt_code  TEXT NOT NULL,
            rcept_no    TEXT,
            stlm_dt     TEXT,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (corp_code, fiscal_year, se, stock_knd)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_div_rank"
        " ON dividends(se, stock_knd, fiscal_year, value)"
    )


def expand_rows(
    rows: list[dict],
    bsns_year: str,
    reprt_code: str,
    stock_code: str | None,
) -> list[tuple]:
    """alotMatter 응답 행 → dividends 11-튜플 목록. 순수: DB 안 씀.

    튜플 순서: (corp_code, stock_code, fiscal_year, se, stock_knd, value,
    value_raw, src_year, reprt_code, rcept_no, stlm_dt). updated_at은 upsert가 찍는다.
    thstrm/frmtrm/lwfr → fiscal_year bsns_year/-1/-2. value_raw ""·None은 버리고
    "-"는 value=None으로 저장한다. stock_knd 없음(None) → ''.
    """
    base = int(bsns_year)
    out: list[tuple] = []
    for r in rows:
        se = r.get("se") or ""
        knd = r.get("stock_knd") or ""
        for offset, key in ((0, "thstrm"), (1, "frmtrm"), (2, "lwfr")):
            raw = r.get(key)
            if raw is None or raw == "":
                continue
            out.append((
                r.get("corp_code"),
                stock_code,
                str(base - offset),
                se,
                knd,
                _to_float(raw),
                raw,
                bsns_year,
                reprt_code,
                r.get("rcept_no"),
                r.get("stlm_dt"),
            ))
    return out


def upsert_dividends(con, tuples: list[tuple]) -> int:
    """최신 보고서(src_year 큰 쪽) 우선 upsert. 실제 쓰기 수 반환.

    같은 PK면 excluded.src_year >= 기존 src_year일 때만 덮어쓴다.
    commit은 호출자가 유지한다.
    """
    if not tuples:
        return 0
    now = _now()
    before = con.total_changes
    con.executemany(
        "INSERT INTO dividends (corp_code, stock_code, fiscal_year, se, stock_knd,"
        " value, value_raw, src_year, reprt_code, rcept_no, stlm_dt, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(corp_code, fiscal_year, se, stock_knd) DO UPDATE SET"
        " stock_code=excluded.stock_code, value=excluded.value,"
        " value_raw=excluded.value_raw, src_year=excluded.src_year,"
        " reprt_code=excluded.reprt_code, rcept_no=excluded.rcept_no,"
        " stlm_dt=excluded.stlm_dt, updated_at=excluded.updated_at"
        " WHERE excluded.src_year >= dividends.src_year",
        [(*t, now) for t in tuples],
    )
    return con.total_changes - before


def run(
    stock_codes: list[str] | None,
    year: str,
    reprt: str,
    skip_existing: bool,
    db_path=None,
) -> dict:
    """corps 유니버스 × alotMatter 1콜/corp 적재. stats 반환.

    stats: total, done, rows, no_data, errors[(stock_code, status)],
    missing[stock_code], stopped.
    020(한도 초과)만 즉시 중단(지금까지 commit 유지). 그 외 DartAPIError는
    errors에 기록 후 계속. corp마다 commit → --skip-existing로 이어받기.
    """
    db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    key = get_api_key()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    stats: dict = {"total": 0, "done": 0, "rows": 0, "no_data": 0,
                   "errors": [], "missing": [], "stopped": False}
    with connect_rw(db_path) as con:
        ensure_schema(con)
        univ = con.execute(
            "SELECT corp_code, stock_code FROM corps ORDER BY corp_code"
        ).fetchall()
        if stock_codes is not None:
            by_stock = {sc: cc for cc, sc in univ}
            targets: list[tuple[str, str]] = []
            seen: set[str] = set()
            for sc in stock_codes:
                if sc in seen:
                    continue
                seen.add(sc)
                if sc in by_stock:
                    targets.append((by_stock[sc], sc))
                else:
                    stats["missing"].append(sc)
        else:
            targets = [(cc, sc) for cc, sc in univ]
        if skip_existing:
            done_set = {
                row[0] for row in con.execute(
                    "SELECT DISTINCT corp_code FROM dividends"
                    " WHERE src_year=? AND reprt_code=?",
                    (year, reprt),
                )
            }
            targets = [t for t in targets if t[0] not in done_set]
        stats["total"] = len(targets)
        print(f"[dividends {year} {reprt}] 대상 {len(targets)}")

        for idx, (corp_code, stock_code) in enumerate(targets, 1):
            try:
                rows = fetch_dart_list(
                    ALOT_API_URL,
                    {"corp_code": corp_code, "bsns_year": year, "reprt_code": reprt},
                    key,
                    session=session,
                    timeout=REQUEST_TIMEOUT,
                )
            except DartAPIError as exc:
                if exc.status == "020":
                    stats["stopped"] = True
                    break
                stats["errors"].append((stock_code, exc.status))
            else:
                if not rows:
                    stats["no_data"] += 1
                else:
                    stats["rows"] += upsert_dividends(
                        con, expand_rows(rows, year, reprt, stock_code)
                    )
                stats["done"] += 1
            con.commit()  # corp 단위 보존 → 중단 시 재실행 이어받기
            time.sleep(DELAY)
            if idx % 100 == 0:
                print(f"[dividends {year} {reprt}][{idx}/{len(targets)}]"
                      f" done={stats['done']} rows={stats['rows']}")

    return stats


def main() -> None:
    p = argparse.ArgumentParser(description="DART 배당에 관한 사항(alotMatter) 적재")
    p.add_argument("--stock-codes", default=None, help="콤마 구분 종목코드 (생략 시 corps 전체)")
    p.add_argument("--year", default="2025", help="사업연도 YYYY")
    p.add_argument("--reprt", default="11011", help="보고서 코드 (11011 사업보고서)")
    p.add_argument("--skip-existing", action="store_true",
                   help="해당 (src_year, reprt_code) 보유 corp 건너뜀")
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = p.parse_args()

    stock_codes = (
        [c.strip() for c in args.stock_codes.split(",") if c.strip()]
        if args.stock_codes else None
    )
    stats = run(stock_codes, args.year, args.reprt, args.skip_existing, args.db)
    print("DONE", stats)  # API 키 절대 print 금지 — stats에 키 없음


if __name__ == "__main__":
    main()
