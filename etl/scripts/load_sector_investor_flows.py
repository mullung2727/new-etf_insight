"""시장·업종 투자자별 순매수 적재 (키움 ka10051 → investor_flows.duckdb).

범위: 2010-01-04 ~ 어제(KST), 코스피(mrkt_tp=0)·코스닥(1), 금액(amt_qty_tp=0), KRX(stex_tp=1).
응답의 모든 업종 행·모든 투자자 필드를 sector_investor_daily 에 저장한다.

순매수 금액 단위는 키움 스펙에 없음 (억원 추정) — KRX 공표치와 대조 필요.

Usage (from etl/):
    uv run python scripts/load_sector_investor_flows.py
    uv run python scripts/load_sector_investor_flows.py --start 20240101 --end 20240131 --market kospi
    uv run python scripts/load_sector_investor_flows.py --refresh --start 20240101 --end 20240102
"""
from __future__ import annotations

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]  # new-etf_insight/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from broker.kiwoom.client import KiwoomError, request  # noqa: E402  (토큰 캐시 공유)
from broker.kiwoom.quotes import _signed_int  # noqa: E402
from new_etf_insight.data_paths import db_dir  # noqa: E402
from research.backtest_daily.data import market_dates  # noqa: E402

TR_SECTOR_INVESTOR = "ka10051"  # 실측 2026-11-08 (tr.py 미등록 — 이 스크립트 전용 로컬 상수)
EP_SECTOR_INVESTOR = "/api/dostk/sect"  # 실측 2026-11-08 (EP_STKINFO 아님)
DEFAULT_START = "20100104"
KST = timezone(timedelta(hours=9))

MARKETS: dict[str, tuple[str, str]] = {
    "kospi": ("KOSPI", "0"),
    "kosdaq": ("KOSDAQ", "1"),
}

RETRY_ATTEMPTS = 3  # KiwoomError 시 재시도 횟수 (초기 1회 + 3회)
RETRY_WAIT = 5  # 재시도 간격 (초)
PROGRESS_EVERY = 100  # 누적 N건마다 진행 출력

# 원 필드 → 컬럼 (sc=금융투자, jnsinkm=기타금융, endw=연기금).
_INVESTOR_MAP: tuple[tuple[str, str], ...] = (
    ("ind_netprps", "ind"),
    ("frgnr_netprps", "frgnr"),
    ("orgn_netprps", "orgn"),
    ("sc_netprps", "sc"),
    ("insrnc_netprps", "insrnc"),
    ("invtrt_netprps", "invtrt"),
    ("bank_netprps", "bank"),
    ("jnsinkm_netprps", "jnsinkm"),
    ("endw_netprps", "endw"),
    ("etc_corp_netprps", "etc_corp"),
    ("native_trmt_frgnr_netprps", "native_trmt_frgnr"),
    ("natn_netprps", "natn"),
    ("samo_fund_netprps", "samo_fund"),
)

_COLS = (
    "date", "market", "inds_cd", "inds_nm", "idx_close", "idx_chg_pct", "trde_qty",
    *(_c for _, _c in _INVESTOR_MAP),
    "fetched_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_investor_daily (
    date VARCHAR,
    market VARCHAR,
    inds_cd VARCHAR,
    inds_nm VARCHAR,
    idx_close DOUBLE,
    idx_chg_pct DOUBLE,
    trde_qty BIGINT,
    ind BIGINT,
    frgnr BIGINT,
    orgn BIGINT,
    sc BIGINT,
    insrnc BIGINT,
    invtrt BIGINT,
    bank BIGINT,
    jnsinkm BIGINT,
    endw BIGINT,
    etc_corp BIGINT,
    native_trmt_frgnr BIGINT,
    natn BIGINT,
    samo_fund BIGINT,
    fetched_at VARCHAR,
    PRIMARY KEY (date, market, inds_cd)
)
"""

# 응답 행 리스트 키 (실측 2026-11-08: base_dt=20150105 → 28행).
_ROW_LIST_KEYS = ("inds_netprps",)


def _default_end() -> str:
    """어제(KST) YYYYMMDD."""
    return (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")


def _extract_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """응답 JSON에서 업종 행 리스트를 꺼낸다."""
    for key in _ROW_LIST_KEYS:
        rows = data.get(key)
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    for value in data.values():  # 예비 감지: 투자자 필드를 가진 첫 리스트
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], dict)
            and any(k.endswith("_netprps") for k in value[0])
        ):
            return value
    return []


def _idx_close(raw: Any) -> float | None:
    """cur_prc → 부호 떼고 /100."""
    v = _signed_int(raw)
    return None if v is None else abs(v) / 100


def _chg_pct(raw: Any) -> float | None:
    """flu_rt → 부호 유지하고 /100."""
    if isinstance(raw, str):
        s = raw.strip().replace(",", "")
        if s.startswith("--"):
            s = "-" + s[2:]
        raw = s
    try:
        return float(str(raw).replace("+", "").strip()) / 100
    except (ValueError, TypeError):
        return None


def parse_row(raw: dict[str, Any], date: str, market: str, fetched_at: str) -> tuple:
    """응답 1행 → _COLS 순서 튜플."""
    out: dict[str, Any] = {
        "date": date,
        "market": market,
        "inds_cd": str(raw.get("inds_cd") or "").strip(),
        "inds_nm": str(raw.get("inds_nm") or "").strip(),
        "idx_close": _idx_close(raw.get("cur_prc")),
        "idx_chg_pct": _chg_pct(raw.get("flu_rt")),
        "trde_qty": _signed_int(raw.get("trde_qty")),
        "fetched_at": fetched_at,
    }
    for src, dst in _INVESTOR_MAP:
        out[dst] = _signed_int(raw.get(src))
    return tuple(out[c] for c in _COLS)


def fetch_day(date: str, mrkt_tp: str) -> list[dict[str, Any]]:
    """하루·한 시장 전 업종 행. cont_yn=Y면 next_key로 이어 받아 합친다.

    과거 일자 조회 지원 (실측 2026-11-08) — 백필 가능.
    """
    body = {"mrkt_tp": mrkt_tp, "amt_qty_tp": "0", "base_dt": date, "stex_tp": "1"}
    rows: list[dict[str, Any]] = []
    cont_yn, next_key = "N", ""
    while True:
        res = request(TR_SECTOR_INVESTOR, EP_SECTOR_INVESTOR, body,
                      cont_yn=cont_yn, next_key=next_key)
        rows.extend(_extract_rows(res.data))
        if res.cont_yn != "Y":
            break
        cont_yn, next_key = "Y", res.next_key
    return rows


def _fetch_with_retry(date: str, mrkt_tp: str) -> list[dict[str, Any]]:
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            return fetch_day(date, mrkt_tp)
        except KiwoomError:
            if attempt >= RETRY_ATTEMPTS:
                raise
            print(f"  {date} 재시도 {attempt + 1}/{RETRY_ATTEMPTS} ({RETRY_WAIT}s 후)")
            time.sleep(RETRY_WAIT)
    raise AssertionError("unreachable")


def _connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_SCHEMA)
    return con


def _existing_pairs(con: duckdb.DuckDBPyConnection) -> set[tuple[str, str]]:
    return {
        (str(r[0]), str(r[1]))
        for r in con.execute("SELECT DISTINCT date, market FROM sector_investor_daily").fetchall()
    }


def save_day(con: duckdb.DuckDBPyConnection, date: str, market: str,
             rows: list[tuple]) -> int:
    """그 날 행 delete 후 insert — 한 트랜잭션."""
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM sector_investor_daily WHERE date=? AND market=?",
                    [date, market])
        if rows:
            con.executemany(
                f"INSERT INTO sector_investor_daily ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' * len(_COLS))})",
                rows,
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="시장·업종 투자자별 순매수 적재 (ka10051)")
    parser.add_argument("--start", default=DEFAULT_START, help="시작일 YYYYMMDD")
    parser.add_argument("--end", default=None, help="종료일 YYYYMMDD (기본 어제 KST)")
    parser.add_argument("--market", choices=["kospi", "kosdaq", "both"], default="both")
    parser.add_argument("--refresh", action="store_true", help="이미 있는 (날짜, 시장)도 덮어씀")
    args = parser.parse_args(argv)
    if args.end is None:
        args.end = _default_end()
    for key in ("start", "end"):
        if len(args.__dict__[key]) != 8 or not args.__dict__[key].isdigit():
            parser.error(f"--{key}는 YYYYMMDD 형식이어야 함")
    return args


def run(args: argparse.Namespace, *, db_path: Path | None = None,
        dates: list[str] | None = None) -> dict[str, Any]:
    """적재 본체. dates/db_path 주입은 테스트용 (실행 시 None → 실데이터)."""
    db_path = db_path or db_dir() / "investor_flows.duckdb"
    if dates is None:
        dates = [d for d in market_dates() if args.start <= d <= args.end]
    markets = [MARKETS[args.market]] if args.market != "both" else list(MARKETS.values())

    con = _connect(db_path)
    try:
        existing = set() if args.refresh else _existing_pairs(con)
        saved = skipped = total_rows = tick = 0
        empty: list[str] = []
        failed: list[str] = []
        fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for date in dates:
            for market, mrkt_tp in markets:
                if (date, market) in existing:
                    skipped += 1
                    continue
                try:
                    raw_rows = _fetch_with_retry(date, mrkt_tp)
                except KiwoomError as exc:
                    failed.append(f"{date} {market}: {exc}")
                    continue
                n = save_day(con, date, market,
                             [parse_row(r, date, market, fetched_at) for r in raw_rows])
                saved += 1
                total_rows += n
                if not raw_rows:
                    empty.append(f"{date} {market}")
                if total_rows // PROGRESS_EVERY > tick:
                    tick = total_rows // PROGRESS_EVERY
                    print(f"{date} {market} 누적 {total_rows}건")
    finally:
        con.close()

    print(f"저장 {saved}건(날짜×시장), 건너뜀 {skipped}, 빈 날 {len(empty)}, 실패 {len(failed)}")
    if empty:
        print("빈 날: " + ", ".join(empty))
    if failed:
        print("실패: " + ", ".join(failed))
    return {"saved": saved, "skipped": skipped, "rows": total_rows,
            "empty": empty, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(ROOT / ".env")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
