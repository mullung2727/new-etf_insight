"""당일 거래량 상위 랭킹 스냅샷 배치 (키움 ka10030 → watchlist.sqlite3).

KRX OpenAPI는 전일까지만 제공하므로 당일(D) 거래량 상위는 키움 REST
`ka10030`(당일거래량상위요청)으로 수집한다. 매 거래일 15:35 이후 실행.

수집 규칙 (PLAN_WATCHLIST_INTRADAY.md):
  - 전체시장, 거래량 정렬, ETF+ETN 제외(mang_stk_incls=16), KRX 단독
  - stk_cd 의 "_AL" 등 suffix 제거 → 6자리 ticker
  - 순수 거래량 상위 N_TOP(30) 슬라이스 — 동전주 컷은 여기서 하지 않는다
    (원본 build_watchlist 로직과 동일하게 후보 산출 단계에서 적용)
  - intraday_ranking 테이블에 (date, rank, ticker, ...) — 같은 날 재실행 시
    해당 날짜 행 전체 삭제 후 재삽입(멱등)

후보 산출 (STEP 2, build_watchlist.find_new_top 와 동치):
  - 당일 top30 − 직전 LOOKBACK(60)거래일 일별 top30 합집합(krx_ohlcv)
  - 동전주(close <= PENNY_MAX) 제외 → watchlist 테이블 upsert

토큰: broker 와 동일한 키움 앱키(루트 .env)로 발급하고, broker 가 쓰는
캐시 파일(broker/.token_cache.json)을 공유한다 — 중복 발급으로 기존 토큰이
무효화되는 사고 방지.

Usage (from etl/):
    uv run python scripts/build_intraday_ranking.py                # 오늘
    uv run python scripts/build_intraday_ranking.py --db-path path/to.duckdb
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sqlite3

import duckdb
import requests
from dotenv import load_dotenv

try:  # 직접 실행(scripts/ on path) / 패키지 import(tests) 양쪽 지원
    from scripts.check_krx_trading_day import trading_day_status
    from scripts.wl_sqlite import connect_rw
except ImportError:
    from check_krx_trading_day import trading_day_status
    from wl_sqlite import connect_rw

ROOT = Path(__file__).resolve().parents[2]                        # new-etf_insight/
ENV_PATH = ROOT / ".env"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"
DEFAULT_KRX_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"
TOKEN_CACHE_PATH = ROOT / "broker" / ".token_cache.json"          # broker 와 공유

_HOSTS = {
    "paper": "https://mockapi.kiwoom.com",
    "real": "https://api.kiwoom.com",
}

EP_RKINFO = "/api/dostk/rkinfo"
TR_TOP_VOLUME = "ka10030"

N_TOP = 30                    # 일별 거래량 상위 N (build_watchlist.py N_TOP 동일)
PENNY_MAX = 500               # 동전주 컷 (build_watchlist.py PENNY_MAX 동일)
LOOKBACK = 60                 # 신규진입 판정 lookback 거래일 (build_watchlist.py LOOKBACK 동일)
REQUEST_TIMEOUT = 15
_TOKEN_REFRESH_SKEW = 600     # 만료 10분 전부터 재발급 (broker auth 동일)

# ka10030 요청 body — PLAN 확정값.
_KA10030_BODY = {
    "mrkt_tp": "000",        # 전체
    "sort_tp": "1",          # 거래량 정렬
    "mang_stk_incls": "16",  # ETF+ETN 제외
    "crd_tp": "0",
    "trde_qty_tp": "0",
    "pric_tp": "0",
    "trde_prica_tp": "0",
    "mrkt_open_tp": "0",     # 장운영구분 전체 (장 마감 후에도 당일 최종값)
    "stex_tp": "1",          # KRX 단독 — 과거 krx_ohlcv와 동일 기준
}

_CREATE_RANKING = """
CREATE TABLE IF NOT EXISTS intraday_ranking (
    date   TEXT,
    rank   INTEGER,
    ticker TEXT,
    name   TEXT,
    volume INTEGER,
    close  INTEGER,
    PRIMARY KEY (date, ticker)
)
"""


# ── 키움 토큰 (broker/.token_cache.json 공유) ────────────────────────────────
def _kiwoom_env() -> str:
    return os.getenv("KIWOOM_ENV", "paper").strip().lower()


def _load_keys() -> tuple[str, str]:
    # env 별 이름(KIWOOM_REAL_* / KIWOOM_PAPER_*)을 먼저 본다 — broker/kiwoom/config.py 와 동일 규칙.
    # 접두사 없는 이름만 보면 KIWOOM_ENV=real 인데 모의 앱키로 실전 호스트를 때려 토큰이 안 나온다.
    prefix = f"KIWOOM_{_kiwoom_env().upper()}_"
    appkey = (
        os.getenv(f"{prefix}APPKEY") or os.getenv("KIWOOM_APPKEY")
        or os.getenv("KIWOON_MOCK_TR_APP_KEY") or ""
    ).strip()
    secret = (
        os.getenv(f"{prefix}SECRETKEY") or os.getenv("KIWOOM_SECRETKEY")
        or os.getenv("KIWOON_MOCK_TR_APP_SECRET") or ""
    ).strip()
    if not appkey or not secret:
        raise RuntimeError(f"키움 앱키 없음 — 루트 .env 의 {prefix}APPKEY/SECRETKEY 확인")
    return appkey, secret


def get_token(host: str, env: str) -> str:
    """캐시 토큰 반환, 만료 임박/불일치 시 재발급 후 캐시 갱신."""
    if TOKEN_CACHE_PATH.exists():
        try:
            data = json.loads(TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
            if (
                data.get("env") == env
                and data.get("token")
                and float(data.get("expires_at", 0)) - _TOKEN_REFRESH_SKEW > time.time()
            ):
                return data["token"]
        except (ValueError, OSError):
            pass

    appkey, secret = _load_keys()
    resp = requests.post(
        f"{host}/oauth2/token",
        json={"grant_type": "client_credentials", "appkey": appkey, "secretkey": secret},
        headers={"content-type": "application/json;charset=UTF-8"},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RuntimeError(f"키움 토큰 발급 실패: {data}")

    # 만료시각: expires_dt(YYYYMMDDHHMMSS, KST) → epoch. 없으면 보수적 12h.
    expires_at = time.time() + 12 * 3600
    raw = str(data.get("expires_dt") or "").strip()
    if len(raw) == 14 and raw.isdigit():
        try:
            expires_at = datetime.strptime(raw, "%Y%m%d%H%M%S").timestamp()
        except ValueError:
            pass
    try:
        TOKEN_CACHE_PATH.write_text(
            json.dumps({"env": env, "token": token, "expires_at": expires_at}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return token


# ── ka10030 호출 + 파싱 ──────────────────────────────────────────────────────
def fetch_top_volume(token: str, host: str, retries: int = 3) -> list[dict[str, Any]]:
    """ka10030 1페이지(100건) 호출 → raw item 리스트. 429는 백오프 재시도."""
    for attempt in range(retries + 1):
        resp = requests.post(
            f"{host}{EP_RKINFO}",
            json=_KA10030_BODY,
            headers={
                "content-type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": TR_TOP_VOLUME,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 429 and attempt < retries:
            time.sleep(1.0 * (attempt + 1))
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    code = data.get("return_code")
    if code not in (None, 0, "0"):
        raise RuntimeError(f"{TR_TOP_VOLUME} return_code={code}: {data.get('return_msg', '')}")
    items = data.get("tdy_trde_qty_upper", [])
    if not items:
        raise RuntimeError(f"{TR_TOP_VOLUME} 응답에 데이터 없음 (휴장일/장전 가능)")
    return items


def _parse_int(val: Any) -> int | None:
    """키움 숫자 문자열('+1200', '-300', '1,234') → int. 실패 시 None."""
    if val is None:
        return None
    try:
        return int(str(val).replace("+", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_ranking(items: list[dict[str, Any]], n_top: int = N_TOP) -> list[tuple]:
    """raw item → (rank, ticker, name, volume, close) 순수 거래량 상위 n_top.

    suffix 제거(005930_AL → 005930), close = cur_prc 절대값.
    동전주 컷은 후보 산출 단계(compute 쪽)에서 — 원본 build_watchlist 와 동일하게
    top30 집합 자체는 가격 무관하게 구성해야 신규진입 판정이 동치가 된다.
    입력은 이미 거래량 내림차순.
    """
    out: list[tuple] = []
    for it in items:
        ticker = str(it.get("stk_cd", "")).split("_")[0].strip()
        volume = _parse_int(it.get("trde_qty"))
        cur_prc = _parse_int(it.get("cur_prc"))
        if not ticker or volume is None or cur_prc is None:
            continue
        out.append((len(out) + 1, ticker, it.get("stk_nm", ""), volume, abs(cur_prc)))
        if len(out) >= n_top:
            break
    return out


# ── DB upsert ────────────────────────────────────────────────────────────────
def upsert_ranking(con: sqlite3.Connection, date: str, rows: list[tuple]) -> int:
    """해당 날짜 행 전체 삭제 후 재삽입 (재실행 시 구성 변동 잔재 방지)."""
    con.execute(_CREATE_RANKING)
    con.execute("DELETE FROM intraday_ranking WHERE date = ?", [date])
    con.executemany(
        "INSERT INTO intraday_ranking VALUES (?, ?, ?, ?, ?, ?)",
        [(date, rank, ticker, name, volume, close) for rank, ticker, name, volume, close in rows],
    )
    return len(rows)


def _is_holiday_stale(con: sqlite3.Connection, date: str, rows: list[tuple]) -> str | None:
    """휴장일 판정: 직전(date 미만 최신) 스냅샷과 (ticker, volume) 완전 동일이면
    해당 직전 날짜 반환, 아니면 None.

    휴장일에 ka10030은 직전 거래일 데이터를 그대로 반환 → 같은 소스(키움
    스냅샷)끼리 비교해야 정확히 일치한다.
    """
    con.execute(_CREATE_RANKING)
    prev_date = con.execute(
        "SELECT MAX(date) FROM intraday_ranking WHERE date < ?", [date]
    ).fetchone()[0]
    if not prev_date:
        return None
    prev = con.execute(
        "SELECT ticker, volume FROM intraday_ranking WHERE date = ? ORDER BY rank",
        [prev_date],
    ).fetchall()
    today = [(ticker, volume) for _rank, ticker, _name, volume, _close in rows]
    return prev_date if today == [tuple(p) for p in prev] else None


def run(db_path: Path | str, date: str | None = None, rows: list[tuple] | None = None) -> int:
    """ka10030 수집 → 휴장일 가드 → intraday_ranking upsert. 적재 행 수 반환.

    휴장일 판정 시 API를 호출하거나 DB를 만들지 않고 0 반환한다. rows 주입은
    API 없는 테스트 경로이므로 달력 선확인을 생략하고 기존 스냅샷 가드만 적용한다.
    """
    date = date or datetime.now().strftime("%Y%m%d")
    if rows is None:
        is_trading, reason = trading_day_status(date)
        if not is_trading:
            print(f"[intraday_ranking] {date}: 휴장일({reason}) — API/저장 skip")
            return 0

    load_dotenv(ENV_PATH)
    env = _kiwoom_env()

    if rows is None:
        host = _HOSTS[env]
        token = get_token(host, env)
        items = fetch_top_volume(token, host)
        rows = parse_ranking(items)
        if len(rows) < N_TOP:
            print(f"[warn] 파싱 후 {len(rows)}건 < {N_TOP}")

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_rw(db_path) as con:
        stale_date = _is_holiday_stale(con, date, rows)
        if stale_date:
            print(f"[intraday_ranking] {date}: 직전 스냅샷({stale_date})과 동일 — 휴장일 판정, skip")
            return 0
        n = upsert_ranking(con, date, rows)
    print(f"[intraday_ranking] env={env} date={date} rows={n} -> {db_path}")
    return n


# ── STEP 2: 신규진입 후보 산출 ───────────────────────────────────────────────
_CREATE_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    date       TEXT,
    stock_code TEXT,
    PRIMARY KEY (date, stock_code)
)
"""


def fetch_past_top_union(
    krx_con: duckdb.DuckDBPyConnection,
    before_date: str,
    lookback: int = LOOKBACK,
    n_top: int = N_TOP,
) -> set[str]:
    """before_date 직전 lookback 거래일의 일별 거래량 top n_top 합집합.

    build_watchlist.get_top_trading_data(nlargest) 와 동치가 되도록
    volume DESC, ticker ASC 정렬 (pandas pivot 컬럼은 ticker 오름차순이라
    동점 시 ticker 빠른 쪽이 먼저 — ROW_NUMBER 동일 기준).
    """
    dates = [
        r[0]
        for r in krx_con.execute(
            "SELECT DISTINCT date FROM ohlcv WHERE date < ? ORDER BY date DESC LIMIT ?",
            [before_date, lookback],
        ).fetchall()
    ]
    if len(dates) < lookback:
        raise RuntimeError(
            f"krx_ohlcv 에 {before_date} 이전 거래일 {len(dates)}일 < lookback {lookback} — "
            "build_krx_ohlcv/build_watchlist 갭필 먼저 실행"
        )
    rows = krx_con.execute(
        """
        SELECT DISTINCT ticker FROM (
            SELECT ticker,
                   ROW_NUMBER() OVER (PARTITION BY date ORDER BY volume DESC, ticker) AS rn
            FROM ohlcv
            WHERE date >= ? AND date < ? AND volume IS NOT NULL
        ) WHERE rn <= ?
        """,
        [dates[-1], before_date, n_top],
    ).fetchall()
    return {r[0] for r in rows}


def compute_candidates(
    today_rows: list[tuple], past_union: set[str], penny_max: int = PENNY_MAX
) -> list[str]:
    """당일 top30 − 과거 합집합, 동전주 컷 (find_new_top + 컷 순서 동일)."""
    return sorted(
        ticker
        for _rank, ticker, _name, _volume, close in today_rows
        if ticker not in past_union and close > penny_max
    )


def upsert_watchlist(con: sqlite3.Connection, date: str, tickers: list[str]) -> int:
    """해당 날짜 행 전체 삭제 후 재삽입 (재실행 시 구성 변동 잔재 방지)."""
    con.execute(_CREATE_WATCHLIST)
    con.execute("DELETE FROM watchlist WHERE date = ?", [date])
    if tickers:
        con.executemany(
            "INSERT INTO watchlist VALUES (?, ?)", [(date, t) for t in tickers]
        )
    return len(tickers)


def run_candidates(wl_db_path: Path | str, krx_db_path: Path | str, date: str) -> list[str]:
    """intraday_ranking(date) 스냅샷 → 신규진입 후보 → watchlist upsert.

    반환: 후보 ticker 정렬 리스트 (0건이면 빈 리스트 — 그날 신규진입 없음).
    """
    krx_con = duckdb.connect(str(krx_db_path), read_only=True)
    try:
        past_union = fetch_past_top_union(krx_con, date)
    finally:
        krx_con.close()

    with connect_rw(Path(wl_db_path)) as con:
        today_rows = con.execute(
            "SELECT rank, ticker, name, volume, close FROM intraday_ranking "
            "WHERE date = ? ORDER BY rank",
            [date],
        ).fetchall()
        if not today_rows:
            raise RuntimeError(f"intraday_ranking 에 {date} 스냅샷 없음 — run() 먼저 실행")
        candidates = compute_candidates(today_rows, past_union)
        upsert_watchlist(con, date, candidates)
    print(f"[candidates] date={date} new={len(candidates)} {candidates}")
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(
        description="당일 거래량 top30 스냅샷 (키움 ka10030) + 신규진입 후보 산출"
    )
    parser.add_argument("--db-path", type=Path, default=None, help="watchlist.sqlite3 경로")
    parser.add_argument("--krx-db-path", type=Path, default=None, help="krx_ohlcv.duckdb 경로")
    parser.add_argument("--date", help="저장 날짜 YYYYMMDD (기본: 오늘). ka10030 자체는 항상 당일 조회")
    parser.add_argument("--no-candidates", action="store_true", help="스냅샷만 적재 (후보 산출 생략)")
    args = parser.parse_args()

    load_dotenv(ENV_PATH)
    db_path = args.db_path or Path(os.environ.get("WATCHLIST_DB_PATH_ABS") or DEFAULT_DB_PATH)
    krx_db_path = args.krx_db_path or Path(
        os.environ.get("KRX_DB_PATH_ABS") or DEFAULT_KRX_DB_PATH
    )
    date = args.date or datetime.now().strftime("%Y%m%d")
    n = run(db_path, date=date)
    if n == 0:
        print("[candidates] 휴장일 — 후보 산출 생략")
        return
    if not args.no_candidates:
        run_candidates(db_path, krx_db_path, date)


if __name__ == "__main__":
    main()
