"""Daily watchlist batch: 급등(거래량) 필터 + LLM 스코어 → watchlist.duckdb.

흐름(krx_hight_volumns.py 로직 이식):
  1. 대상 거래일 D 결정 (기본: 최신 거래일).
  2. D-60거래일 ~ D 구간 ohlcv 누락분을 build_krx_ohlcv.ensure_ohlcv 로 자기치유 갭필.
  3. krx_ohlcv 에서 close/volume 피벗 로드 (index=date, columns=ticker).
  4. 통합 거래량 상위 30 (get_top_trading_data) → 60거래일 신규진입 (find_new_top)
     → 종가 ≤ 500 제외 → watchlist 테이블 upsert.
  5. REPORTS_DIR/volume_spike_*.notion.json 스캔 → llm_scores 테이블 upsert.

데이터 출처: STEP1 참조. ohlcv 본체는 KRX OpenAPI, 거래일 달력은 pykrx(005930).

Usage (from etl/):
    uv run python scripts/build_watchlist.py                 # 최신 거래일
    uv run python scripts/build_watchlist.py --date 20260604 # 특정 거래일
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import duckdb
import pandas as pd

# scripts/ 가 sys.path[0] 이므로 동일 디렉토리 STEP1 모듈을 직접 import.
from build_krx_ohlcv import ensure_ohlcv, get_trading_calendar, load_api_key

ROOT = Path(__file__).resolve().parents[2]                       # new-etf_insight/
ENV_PATH = ROOT / ".env"
DEFAULT_KRX_DB = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"
DEFAULT_WATCHLIST_DB = Path(__file__).resolve().parents[1] / "db" / "watchlist.duckdb"
DEFAULT_REPORTS_DIR = Path(r"C:\Users\mullu\.openclaw\workspace\reports")

LOOKBACK = 60                 # 신규진입 판정 lookback (거래일)
N_TOP = 30                    # 통합 거래량 상위 N
PENNY_MAX = 500               # 동전주 컷 (종가 ≤ 500 제외)
GAPFILL_CAL_DAYS = 100        # D 기준 갭필 캘린더 범위 (≈68 거래일, lookback 60 확보)

_CREATE_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    date       VARCHAR,
    stock_code VARCHAR,
    PRIMARY KEY (date, stock_code)
)
"""

_CREATE_LLM_SCORES = """
CREATE TABLE IF NOT EXISTS llm_scores (
    date           VARCHAR,
    ticker         VARCHAR,
    name           VARCHAR,
    ratio          DOUBLE,
    today_volume   BIGINT,
    avg5_volume    BIGINT,
    trading_value  BIGINT,
    close          INTEGER,
    score          INTEGER,
    category       VARCHAR,
    reason_summary TEXT,
    final_opinion  TEXT,
    evidence_board TEXT,
    evidence_news  TEXT,
    evidence_web   TEXT,
    sources        TEXT,
    PRIMARY KEY (date, ticker)
)
"""


# ── 급등 필터 (krx_hight_volumns.py 이식) ─────────────────────────────────────
def get_top_trading_data(df: pd.DataFrame, n_top: int = N_TOP) -> pd.DataFrame:
    """날짜별 거래량 상위 n_top 종목 → long-format (date, ticker, volume)."""
    parts = []
    for date in df.index:
        top_n = df.loc[date].nlargest(n_top)          # NaN 자동 제외
        parts.append(
            pd.DataFrame({"date": date, "ticker": top_n.index, "volume": top_n.values})
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["date", "ticker", "volume"]
    )


def find_new_top(top_df: pd.DataFrame, date_list: list[str], lookback: int = LOOKBACK) -> dict[int, list[str]]:
    """date_list[i] 의 top 집합 − [i-lookback, i) top 집합 = 신규진입 종목."""
    target: dict[int, list[str]] = {}
    for i in range(lookback, len(date_list)):
        prev_idx = i - lookback
        hist = set(
            top_df.loc[
                (top_df.date >= date_list[prev_idx]) & (top_df.date < date_list[i])
            ].ticker.values
        )
        today = set(top_df.loc[top_df.date == date_list[i]].ticker.values)
        new_top = sorted(today - hist)
        if new_top:
            target[i] = new_top
    return target


def compute_watchlist(con: duckdb.DuckDBPyConnection, from_date: str, to_date: str) -> dict[str, list[str]]:
    """krx_ohlcv 에서 급등 필터 → {YYYYMMDD: [stock_code]}.

    종가 ≤ 500(동전주) 제외. lookback 60거래일 신규진입만.
    """
    rows = con.execute(
        "SELECT date, ticker, close, volume FROM ohlcv WHERE date BETWEEN ? AND ?",
        [from_date, to_date],
    ).fetchall()
    if not rows:
        return {}

    df = pd.DataFrame(rows, columns=["date", "ticker", "close", "volume"])
    volumes = df.pivot(index="date", columns="ticker", values="volume").sort_index()
    closes = df.pivot(index="date", columns="ticker", values="close").sort_index()
    date_list = list(volumes.index)

    top_df = get_top_trading_data(volumes, N_TOP)
    target = find_new_top(top_df, date_list, LOOKBACK)

    result: dict[str, list[str]] = {}
    for i, tickers in target.items():
        date_str = date_list[i]
        kept = []
        for t in tickers:
            close = closes.at[date_str, t] if t in closes.columns else None
            if pd.notna(close) and close > PENNY_MAX:
                kept.append(t)
        if kept:
            result[date_str] = kept
    return result


def upsert_watchlist(con: duckdb.DuckDBPyConnection, watchlist: dict[str, list[str]]) -> int:
    con.execute(_CREATE_WATCHLIST)
    rows = [(d, code) for d, codes in watchlist.items() for code in codes]
    if rows:
        con.executemany("INSERT OR REPLACE INTO watchlist VALUES (?, ?)", rows)
    return len(rows)


# ── LLM 스코어 (volume_spike_*.notion.json) ──────────────────────────────────
def _to_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def load_llm_scores(reports_dir: Path) -> list[tuple]:
    """volume_spike_*.notion.json 전체 → llm_scores row 튜플 리스트.

    item 키 드리프트(구 json은 ratio/volume/close/category 없음)는 .get() 으로 None.
    파일 최상위 sources(없으면 source_data)를 JSON 문자열로 각 row 에 복사.
    """
    out: list[tuple] = []
    for path in sorted(reports_dir.glob("volume_spike_*.notion.json")):
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        prov = doc.get("sources") or doc.get("source_data")
        sources_str = json.dumps(prov, ensure_ascii=False) if prov is not None else None
        for it in doc.get("items", []):
            date = str(it.get("date", "")).replace("-", "")
            ticker = it.get("ticker")
            if not date or not ticker:
                continue
            out.append(
                (
                    date,
                    ticker,
                    it.get("name"),
                    it.get("ratio"),
                    _to_int(it.get("today_volume")),
                    _to_int(it.get("avg5_volume")),
                    _to_int(it.get("trading_value")),
                    _to_int(it.get("close")),
                    _to_int(it.get("score")),
                    it.get("category"),
                    it.get("reason_summary"),
                    it.get("final_opinion"),
                    it.get("evidence_board"),
                    it.get("evidence_news"),
                    it.get("evidence_web"),
                    sources_str,
                )
            )
    return out


def upsert_llm_scores(con: duckdb.DuckDBPyConnection, rows: list[tuple]) -> int:
    con.execute(_CREATE_LLM_SCORES)
    if rows:
        placeholders = ",".join(["?"] * 16)
        con.executemany(f"INSERT OR REPLACE INTO llm_scores VALUES ({placeholders})", rows)
    return len(rows)


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Build daily watchlist + LLM scores")
    parser.add_argument("--date", help="대상 거래일 YYYYMMDD (기본: 최신 거래일)")
    parser.add_argument("--krx-db-path", type=Path)
    parser.add_argument("--watchlist-db-path", type=Path)
    parser.add_argument("--reports-dir", type=Path)
    parser.add_argument("--force", action="store_true",
                        help="ohlcv 적재된 거래일도 KRX에서 다시 받아 덮어쓰기")
    args = parser.parse_args()

    load_dotenv_paths()
    krx_db = args.krx_db_path or Path(os.environ.get("KRX_DB_PATH_ABS") or DEFAULT_KRX_DB)
    wl_db = args.watchlist_db_path or Path(os.environ.get("WATCHLIST_DB_PATH_ABS") or DEFAULT_WATCHLIST_DB)
    reports_dir = args.reports_dir or Path(os.environ.get("REPORTS_DIR") or DEFAULT_REPORTS_DIR)

    key = load_api_key()
    today = datetime.now().strftime("%Y%m%d")
    gapfill_from = (datetime.now() - timedelta(days=GAPFILL_CAL_DAYS)).strftime("%Y%m%d")

    # 대상 거래일 D = 인자 또는 달력 최신 거래일
    if args.date:
        to_date = args.date
        gapfill_from = (datetime.strptime(args.date, "%Y%m%d") - timedelta(days=GAPFILL_CAL_DAYS)).strftime("%Y%m%d")
    else:
        calendar = get_trading_calendar(gapfill_from, today)
        if not calendar:
            raise RuntimeError("거래일 달력 비어있음 — 날짜 범위 확인")
        to_date = calendar[-1]

    krx_db.parent.mkdir(parents=True, exist_ok=True)
    wl_db.parent.mkdir(parents=True, exist_ok=True)

    # 1) 자기치유 갭필 (krx_ohlcv write)
    krx_con = duckdb.connect(str(krx_db))
    try:
        stats = ensure_ohlcv(krx_con, gapfill_from, to_date, key, force=args.force)
        print(
            f"[gapfill {gapfill_from}~{to_date}] calendar={stats['calendar_days']} "
            f"held={stats['held_days']} missing={stats['missing_days']} "
            f"fetched={stats['fetched_days']}d/{stats['inserted_rows']}rows"
        )
        # 2) 급등 필터
        watchlist = compute_watchlist(krx_con, gapfill_from, to_date)
    finally:
        krx_con.close()

    # 3) watchlist + llm_scores upsert (watchlist.duckdb write)
    score_rows = load_llm_scores(reports_dir)
    wl_con = duckdb.connect(str(wl_db))
    try:
        n_wl = upsert_watchlist(wl_con, watchlist)
        n_sc = upsert_llm_scores(wl_con, score_rows)
    finally:
        wl_con.close()

    dates = sorted(watchlist)
    print(
        f"[watchlist] target={to_date} dates={len(watchlist)} "
        f"({dates[0] if dates else '-'}~{dates[-1] if dates else '-'}) "
        f"codes={n_wl} → {wl_db}"
    )
    print(f"[llm_scores] files→rows={n_sc} (reports={reports_dir})")


def load_dotenv_paths() -> None:
    """REPORTS_DIR/KRX_DB_PATH/WATCHLIST_DB_PATH 를 .env 에서 로드(상대→절대)."""
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
    for env_key, abs_key in (
        ("KRX_DB_PATH", "KRX_DB_PATH_ABS"),
        ("WATCHLIST_DB_PATH", "WATCHLIST_DB_PATH_ABS"),
    ):
        val = os.environ.get(env_key)
        if val:
            p = Path(val)
            os.environ[abs_key] = str(p if p.is_absolute() else (ROOT / val).resolve())


if __name__ == "__main__":
    main()
