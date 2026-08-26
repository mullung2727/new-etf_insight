"""VFS 신호 배치: 거래량 폭발 장대양봉 다음날 눌림 → watchlist.sqlite3 의 vfs 테이블.

신호 정의 (stock_scout/krx_vfs.py 로직 이식):
  D일  : 거래량 > 20거래일 이동평균 x 5, 종가 > 시가 x 1.1 (장대양봉)
  D+1일: 종가 > (D 종가 + D 시가)/2  (양봉 몸통 중간 위 유지)
         종가 < 시가 x 0.99          (음봉)
         거래량 < D 거래량 x 0.2     (거래량 급감)
  → (D+1 날짜, 종목코드) 를 vfs 에 적재.

거래정지 가드: 거래정지 구간은 ohlcv 에 행이 없어 종목별 윈도우가 정지일을 건너뛴다.
그대로 두면 20거래일 평균이 실제로는 두 달치를 걸치고, D+1 이 몇 주 뒤가 된다.
(실측 1.6% / 0.8%, 재개 후 미조정 가격 점프가 섞여 손실 쪽으로 치우침)
따라서 이동평균 구간과 D→D+1 이 시장 기준 연속 거래일인 신호만 남긴다.

데이터 출처: krx_ohlcv.duckdb (KRX OpenAPI). 원본의 pykrx 수집부는 ensure_ohlcv 갭필로 대체.
pandas rolling/mask 대신 DuckDB 윈도우 SQL 1개로 산출.

Usage (from etl/):
    uv run python scripts/build_vfs.py                 # 최신 거래일
    uv run python scripts/build_vfs.py --date 20260604 # 특정 거래일
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  (cp949 가드 + path)

import argparse
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from build_krx_ohlcv import ensure_ohlcv, get_trading_calendar, load_api_key
from wl_sqlite import connect_rw

ROOT = Path(__file__).resolve().parents[2]                       # new-etf_insight/
ENV_PATH = ROOT / ".env"
DEFAULT_KRX_DB = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.sqlite3"

VOL_MA_DAYS = 20              # 거래량 이동평균 기간 (거래일)
VOL_SPIKE = 5.0               # D일 거래량 / 이동평균 배수 하한
BODY_UP = 1.1                 # D일 종가/시가 하한 (장대양봉)
NEXT_BODY_DOWN = 0.99         # D+1 종가/시가 상한 (음봉)
NEXT_VOL_DRY = 0.2            # D+1 거래량 / D일 거래량 상한 (거래량 급감)
GAPFILL_CAL_DAYS = 60         # D 기준 갭필 캘린더 범위 (≈41 거래일, VOL_MA_DAYS 20 확보)

_CREATE_VFS = """
CREATE TABLE IF NOT EXISTS vfs (
    date       TEXT,
    stock_code TEXT,
    PRIMARY KEY (date, stock_code)
)
"""

# 윈도우는 ticker 별 정렬(seq)로 계산한다.
#   vol_ma20 : 당일 포함 직전 20거래일 거래량 평균 (pandas rolling(20).mean() 과 동일)
#   n20      : 윈도우 실제 행 수 — 20 미만이면 pandas 의 NaN 구간이므로 제외
#   nx_*     : LEAD = 해당 종목의 다음 거래일 값 (원본의 trading_dates[i+1] 대응)
_VFS_SQL = f"""
WITH d AS (
    SELECT date, ROW_NUMBER() OVER (ORDER BY date) AS seq
    FROM (SELECT DISTINCT date FROM ohlcv WHERE date BETWEEN ? AND ?)
),
o AS (
    SELECT o.date, o.ticker, o.open, o.close, o.volume, d.seq
    FROM ohlcv o JOIN d USING (date)
    WHERE o.date BETWEEN ? AND ?
),
w AS (
    SELECT *,
        AVG(volume) OVER (PARTITION BY ticker ORDER BY seq
                          ROWS BETWEEN {VOL_MA_DAYS - 1} PRECEDING AND CURRENT ROW) AS vol_ma,
        COUNT(volume) OVER (PARTITION BY ticker ORDER BY seq
                          ROWS BETWEEN {VOL_MA_DAYS - 1} PRECEDING AND CURRENT ROW) AS n_ma,
        MIN(seq)     OVER (PARTITION BY ticker ORDER BY seq
                          ROWS BETWEEN {VOL_MA_DAYS - 1} PRECEDING AND CURRENT ROW) AS ma_first_seq,
        LEAD(seq)    OVER (PARTITION BY ticker ORDER BY seq) AS nx_seq,
        LEAD(date)   OVER (PARTITION BY ticker ORDER BY seq) AS nx_date,
        LEAD(open)   OVER (PARTITION BY ticker ORDER BY seq) AS nx_open,
        LEAD(close)  OVER (PARTITION BY ticker ORDER BY seq) AS nx_close,
        LEAD(volume) OVER (PARTITION BY ticker ORDER BY seq) AS nx_volume
    FROM o
)
SELECT nx_date AS date, ticker
FROM w
WHERE n_ma = {VOL_MA_DAYS}
  AND seq - ma_first_seq = {VOL_MA_DAYS - 1}   -- 이동평균 구간이 연속 거래일 (거래정지 건너뜀 배제)
  AND nx_seq = seq + 1                          -- D+1 이 시장 기준 실제 다음 거래일
  AND volume > vol_ma * {VOL_SPIKE}
  AND close > open * {BODY_UP}
  AND nx_date IS NOT NULL
  AND nx_open > 0
  AND nx_close > (close + open) * 0.5
  AND nx_close < nx_open * {NEXT_BODY_DOWN}
  AND nx_volume < volume * {NEXT_VOL_DRY}
ORDER BY date, ticker
"""


def compute_vfs(con: duckdb.DuckDBPyConnection, from_date: str, to_date: str) -> dict[str, list[str]]:
    """krx_ohlcv 에서 VFS 신호 산출 → {YYYYMMDD(D+1): [stock_code]}."""
    rows = con.execute(_VFS_SQL, [from_date, to_date, from_date, to_date]).fetchall()
    result: dict[str, list[str]] = {}
    for date_str, ticker in rows:
        result.setdefault(date_str, []).append(ticker)
    return result


def upsert_vfs(con: sqlite3.Connection, signals: dict[str, list[str]]) -> int:
    con.execute(_CREATE_VFS)
    rows = [(d, code) for d, codes in signals.items() for code in codes]
    if rows:
        con.executemany("INSERT OR REPLACE INTO vfs VALUES (?, ?)", rows)
    return len(rows)


def load_dotenv_paths() -> None:
    """KRX_DB_PATH / WATCHLIST_DB_PATH 를 .env 에서 로드(상대→절대)."""
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
    for env_key, abs_key in (
        ("KRX_DB_PATH", "KRX_DB_PATH_ABS"),
        ("WATCHLIST_DB_PATH", "WATCHLIST_DB_PATH_ABS"),
    ):
        value = os.environ.get(env_key)
        if value:
            os.environ[abs_key] = str((ROOT / value).resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Build VFS signals into watchlist.sqlite3")
    parser.add_argument("--date", help="대상 거래일 YYYYMMDD (기본: 최신 거래일)")
    parser.add_argument("--krx-db-path", type=Path)
    parser.add_argument("--watchlist-db-path", type=Path)
    parser.add_argument("--force", action="store_true",
                        help="ohlcv 적재된 거래일도 KRX에서 다시 받아 덮어쓰기")
    args = parser.parse_args()

    load_dotenv_paths()
    krx_db = args.krx_db_path or Path(os.environ.get("KRX_DB_PATH_ABS") or DEFAULT_KRX_DB)
    wl_db = args.watchlist_db_path or Path(os.environ.get("WATCHLIST_DB_PATH_ABS") or DEFAULT_WATCHLIST_DB)

    key = load_api_key()
    today = datetime.now().strftime("%Y%m%d")
    base = datetime.strptime(args.date, "%Y%m%d") if args.date else datetime.now()
    gapfill_from = (base - timedelta(days=GAPFILL_CAL_DAYS)).strftime("%Y%m%d")

    if args.date:
        to_date = args.date
    else:
        calendar = get_trading_calendar(gapfill_from, today)
        if not calendar:
            raise RuntimeError("거래일 달력 비어있음 — 날짜 범위 확인")
        to_date = calendar[-1]

    krx_db.parent.mkdir(parents=True, exist_ok=True)
    wl_db.parent.mkdir(parents=True, exist_ok=True)

    krx_con = duckdb.connect(str(krx_db))
    try:
        stats = ensure_ohlcv(krx_con, gapfill_from, to_date, key, force=args.force)
        print(
            f"[gapfill {gapfill_from}~{to_date}] calendar={stats['calendar_days']} "
            f"held={stats['held_days']} missing={stats['missing_days']} "
            f"fetched={stats['fetched_days']}d/{stats['inserted_rows']}rows"
        )
        signals = compute_vfs(krx_con, gapfill_from, to_date)
    finally:
        krx_con.close()

    with connect_rw(wl_db) as wl_con:
        n = upsert_vfs(wl_con, signals)

    dates = sorted(signals)
    print(
        f"[vfs] target={to_date} dates={len(signals)} "
        f"({dates[0] if dates else '-'}~{dates[-1] if dates else '-'}) "
        f"codes={n} → {wl_db}"
    )


if __name__ == "__main__":
    main()
