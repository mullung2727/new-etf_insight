"""종목별 투자자 일별 순매수 적재 (키움 ka10059 → investor_flows.duckdb).

범위: 2010-01-04 ~ 어제(KST), 전일 시총 상위 200 합집합 유니버스.
종목당 dt=끝날짜부터 과거로 100행씩(dt 내림차순), cont_yn=Y면 next_key로 이어 받는다.

순매수·거래대금 단위는 키움 스펙에 없음 (acc_trde_prica 백만원 추정) — KRX 공표치와 대조 필요.

Usage (from etl/):
    uv run python scripts/load_stock_investor_flows.py
    uv run python scripts/load_stock_investor_flows.py --start 20240101 --end 20240131 --limit 5
    uv run python scripts/load_stock_investor_flows.py --tickers 005930,000660 --refresh
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
from research.backtest_daily.data import load_px  # noqa: E402

TR_STOCK_INVESTOR = "ka10059"  # 실측 2026-11-08 (tr.py 미등록 — 이 스크립트 전용 로컬 상수)
EP_STOCK_INVESTOR = "/api/dostk/stkinfo"  # 실측 2026-11-08
DEFAULT_START = "20100104"
DEFAULT_N = 200
KST = timezone(timedelta(hours=9))

RETRY_ATTEMPTS = 3  # KiwoomError 시 재시도 횟수 (초기 1회 + 3회)
RETRY_WAIT = 5  # 재시도 간격 (초)

# 원 필드 → 컬럼.
_INVESTOR_MAP: tuple[tuple[str, str], ...] = (
    ("ind_invsr", "ind"),
    ("frgnr_invsr", "frgnr"),
    ("orgn", "orgn"),
    ("fnnc_invt", "fnnc_invt"),
    ("insrnc", "insrnc"),
    ("invtrt", "invtrt"),
    ("etc_fnnc", "etc_fnnc"),
    ("bank", "bank"),
    ("penfnd_etc", "penfnd_etc"),
    ("samo_fund", "samo_fund"),
    ("natn", "natn"),
    ("etc_corp", "etc_corp"),
    ("natfor", "natfor"),
)

_COLS = (
    "date", "ticker", "close", "chg_pct", "acc_trde_qty", "acc_trde_prica",
    *(_c for _, _c in _INVESTOR_MAP),
    "fetched_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_investor_daily (
    date VARCHAR,
    ticker VARCHAR,
    close BIGINT,
    chg_pct DOUBLE,
    acc_trde_qty BIGINT,
    acc_trde_prica BIGINT,
    ind BIGINT,
    frgnr BIGINT,
    orgn BIGINT,
    fnnc_invt BIGINT,
    insrnc BIGINT,
    invtrt BIGINT,
    etc_fnnc BIGINT,
    bank BIGINT,
    penfnd_etc BIGINT,
    samo_fund BIGINT,
    natn BIGINT,
    etc_corp BIGINT,
    natfor BIGINT,
    fetched_at VARCHAR,
    PRIMARY KEY (date, ticker)
)
"""

# "end"는 예약어라 인용. status ∈ {'ok','empty','error'}.
_DONE_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_investor_done (
    ticker VARCHAR PRIMARY KEY,
    start VARCHAR,
    "end" VARCHAR,
    rows INT,
    status VARCHAR,
    fetched_at VARCHAR
)
"""

# 응답 행 리스트 키 (실측 2026-11-08: 한 번에 100행, dt 내림차순).
_ROW_LIST_KEYS = ("stk_invsr_orgn",)


def _default_end() -> str:
    """어제(KST) YYYYMMDD."""
    return (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")


def universe(px: Any, n: int = DEFAULT_N, start: str = DEFAULT_START,
             end: str | None = None) -> list[str]:
    """전 거래일 시총 상위 n 합집합 (정렬).

    px는 load_px() DataFrame (date/ticker/market_cap 사용).
    start~end 각 거래일 d마다 전 거래일 market_cap 상위 n을 모아 합친다.
    당일 자신의 시총은 쓰지 않는다 (d의 편입 여부는 전일 순위 기준).
    """
    df = px.copy()
    df["date"] = df["date"].astype(str)
    dates = sorted(df["date"].unique())
    groups = {d: g for d, g in df.groupby("date")}
    out: set[str] = set()
    for i, d in enumerate(dates):
        if d < start or (end is not None and d > end) or i == 0:
            continue
        prev = groups[dates[i - 1]]
        out.update(prev.nlargest(n, "market_cap")["ticker"].astype(str).tolist())
    return sorted(out)


def _extract_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """응답 JSON에서 종목 일별 행 리스트를 꺼낸다. 키 없거나 리스트 아니면 KiwoomError."""
    for key in _ROW_LIST_KEYS:
        if key in data:
            rows = data[key]
            if not isinstance(rows, list):
                raise KiwoomError(f"예상치 못한 응답 형식: {key}가 리스트가 아님")
            return [r for r in rows if isinstance(r, dict)]
    raise KiwoomError(f"예상치 못한 응답 형식: {', '.join(_ROW_LIST_KEYS)} 키 없음")


def _close(raw: Any) -> int | None:
    """cur_prc → 부호 뗀 값."""
    v = _signed_int(raw)
    return None if v is None else abs(v)


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


def parse_row(raw: dict[str, Any], ticker: str, fetched_at: str) -> tuple | None:
    """응답 1행 → _COLS 순서 튜플. dt 없으면 None (버림)."""
    dt = str(raw.get("dt") or "").strip()
    if len(dt) != 8 or not dt.isdigit():
        return None
    out: dict[str, Any] = {
        "date": dt,
        "ticker": ticker,
        "close": _close(raw.get("cur_prc")),
        "chg_pct": _chg_pct(raw.get("flu_rt")),
        "acc_trde_qty": _signed_int(raw.get("acc_trde_qty")),
        "acc_trde_prica": _signed_int(raw.get("acc_trde_prica")),
        "fetched_at": fetched_at,
    }
    for src, dst in _INVESTOR_MAP:
        out[dst] = _signed_int(raw.get(src))
    return tuple(out[c] for c in _COLS)


def fetch_ticker(code: str, start: str, end: str) -> list[dict[str, Any]]:
    """한 종목 [start, end] 일별 행. cont_yn=Y면 next_key로 이어 받아 합친다.

    dt=end부터 과거로 내려가며, 시작일보다 과거 행이 나오면 멈춘다 (dt 내림차순).
    """
    body = {"dt": end, "stk_cd": code, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"}
    rows: list[dict[str, Any]] = []
    cont_yn, next_key = "N", ""
    while True:
        res = request(TR_STOCK_INVESTOR, EP_STOCK_INVESTOR, body,
                      cont_yn=cont_yn, next_key=next_key)
        for raw in _extract_rows(res.data):
            dt = str(raw.get("dt") or "").strip()
            if dt and dt < start:
                return rows
            rows.append(raw)
        if res.cont_yn != "Y":
            break
        cont_yn, next_key = "Y", res.next_key
    return rows


def _fetch_with_retry(code: str, start: str, end: str) -> list[dict[str, Any]]:
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            return fetch_ticker(code, start, end)
        except KiwoomError:
            if attempt >= RETRY_ATTEMPTS:
                raise
            print(f"  {code} 재시도 {attempt + 1}/{RETRY_ATTEMPTS} ({RETRY_WAIT}s 후)")
            time.sleep(RETRY_WAIT)
    raise AssertionError("unreachable")


def _connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(_SCHEMA)
    con.execute(_DONE_SCHEMA)
    return con


def _done_map(con: duckdb.DuckDBPyConnection, start: str, end: str) -> dict[str, str]:
    """같은 start·end로 ok/empty인 종목 → 상태."""
    return {
        str(r[0]): str(r[1])
        for r in con.execute(
            "SELECT ticker, status FROM stock_investor_done "
            "WHERE start=? AND \"end\"=? AND status IN ('ok', 'empty')",
            [start, end],
        ).fetchall()
    }


def _mark_done(con: duckdb.DuckDBPyConnection, ticker: str, start: str, end: str,
               n: int, status: str, fetched_at: str) -> None:
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM stock_investor_done WHERE ticker=?", [ticker])
        con.execute(
            "INSERT INTO stock_investor_done (ticker, start, \"end\", rows, status, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [ticker, start, end, n, status, fetched_at],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def save_ticker(con: duckdb.DuckDBPyConnection, ticker: str, start: str, end: str,
                rows: list[tuple]) -> int:
    """그 종목 [start, end] 행 delete 후 insert — 한 트랜잭션."""
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("DELETE FROM stock_investor_daily WHERE ticker=? AND date>=? AND date<=?",
                    [ticker, start, end])
        if rows:
            con.executemany(
                f"INSERT INTO stock_investor_daily ({', '.join(_COLS)}) "
                f"VALUES ({', '.join('?' * len(_COLS))})",
                rows,
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="종목별 투자자 일별 순매수 적재 (ka10059)")
    parser.add_argument("--start", default=DEFAULT_START, help="시작일 YYYYMMDD")
    parser.add_argument("--end", default=None, help="종료일 YYYYMMDD (기본 어제 KST)")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help="전일 시총 상위 N (유니버스)")
    parser.add_argument("--limit", type=int, default=None, help="앞 N종목만 (시험용)")
    parser.add_argument("--tickers", default=None, help="쉼표 구분 종목코드 (지정 시 유니버스 대신)")
    parser.add_argument("--refresh", action="store_true", help="done(ok/empty) 종목도 다시 받음")
    args = parser.parse_args(argv)
    if args.end is None:
        args.end = _default_end()
    for key in ("start", "end"):
        try:
            datetime.strptime(args.__dict__[key], "%Y%m%d")
        except (ValueError, TypeError):
            parser.error(f"--{key}는 YYYYMMDD 형식이어야 함")
    if args.start > args.end:
        parser.error("--start는 --end보다 늦을 수 없음")
    if args.n < 1:
        parser.error("--n은 1 이상이어야 함")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit은 1 이상이어야 함")
    return args


def run(args: argparse.Namespace, *, db_path: Path | None = None,
        tickers: list[str] | None = None) -> dict[str, Any]:
    """적재 본체. tickers/db_path 주입은 테스트용 (실행 시 None → 실데이터)."""
    db_path = db_path or db_dir() / "investor_flows.duckdb"
    if tickers is None:
        if args.tickers:
            tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
        else:
            tickers = universe(load_px(), n=args.n, start=args.start, end=args.end)
    if args.limit is not None:
        tickers = tickers[:args.limit]
    total = len(tickers)
    print(f"{'지정' if args.tickers else '유니버스'} {total}종목 ({args.start}~{args.end})")

    con = _connect(db_path)
    try:
        done = {} if args.refresh else _done_map(con, args.start, args.end)
        saved = skipped = total_rows = 0
        empty: list[str] = []
        failed: list[str] = []
        fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for i, code in enumerate(tickers, 1):
            if code in done:
                skipped += 1
                print(f"[{i}/{total}] {code} 건너뜀(done {done[code]})")
                continue
            t0 = time.time()
            try:
                raw_rows = _fetch_with_retry(code, args.start, args.end)
            except KiwoomError as exc:
                failed.append(f"{code}: {exc}")
                _mark_done(con, code, args.start, args.end, 0, "error", fetched_at)
                print(f"[{i}/{total}] {code} 실패 ({time.time() - t0:.1f}s)")
                continue
            rows = [r for r in (parse_row(x, code, fetched_at) for x in raw_rows)
                    if r is not None]
            if raw_rows and not rows:
                msg = f"{code}: 잘못된 dt (응답 {len(raw_rows)}행 파싱 불가)"
                failed.append(msg)
                _mark_done(con, code, args.start, args.end, 0, "error", fetched_at)
                print(f"[{i}/{total}] {code} 실패 ({time.time() - t0:.1f}s) — {msg}")
                continue
            n = save_ticker(con, code, args.start, args.end, rows)
            _mark_done(con, code, args.start, args.end, n,
                       "ok" if n else "empty", fetched_at)
            saved += 1
            total_rows += n
            if not n:
                empty.append(code)
            print(f"[{i}/{total}] {code} {n}행 ({time.time() - t0:.1f}s)")
    finally:
        con.close()

    print(f"저장 {saved}종목, 건너뜀 {skipped}, 빈 종목 {len(empty)}, "
          f"실패 {len(failed)}, 총 {total_rows}행")
    if empty:
        print("빈 종목: " + ", ".join(empty))
    if failed:
        print("실패: " + ", ".join(failed))
    return {"saved": saved, "skipped": skipped, "rows": total_rows,
            "empty": empty, "failed": failed}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(ROOT / ".env")
    result = run(args)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
