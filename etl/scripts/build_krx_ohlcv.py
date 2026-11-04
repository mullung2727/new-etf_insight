"""Build/refresh the KRX all-ticker daily OHLCV cache (krx_ohlcv.duckdb).

데이터 출처: KRX OpenAPI (data-dbg.krx.co.kr, AUTH_KEY 헤더) 단일.
KRX OpenAPI는 기준일자(basDd) 단위 조회만 지원 — 기간/종목 필터 없음.

거래일 판정은 자기학습 방식:
  - 평일을 후보일로 잡고, 캐시에 없는 날만 KRX 호출.
  - 응답이 비어 있는데 그보다 나중 날짜에 데이터가 이미 있으면 휴장일 확정
    → holidays 테이블에 기록, 이후 실행에서 절대 재호출하지 않음.
  - 나중 데이터가 없는 빈 날짜(당일 미공시 가능)는 기록하지 않고 다음 실행에서 재시도.

캐시에 없는 거래일만 KRX에서 가져와 upsert 한다(자기치유 갭필).
분봉 선적재 날짜(ohlcv_provisional)는 미적재로 보고 KRX 도착 시 reconcile_day 로 대조·교정한다.

Usage (from etl/):
    uv run python scripts/build_krx_ohlcv.py                       # 최근 ~100일 → 오늘
    uv run python scripts/build_krx_ohlcv.py --from 20250101 --to 20250131
    uv run python scripts/build_krx_ohlcv.py --date 20250102       # 단일 거래일
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import duckdb
import requests
from dotenv import load_dotenv
from new_etf_insight.data_paths import db_dir

ROOT = Path(__file__).resolve().parents[2]                       # new-etf_insight/
DEFAULT_DB_PATH = db_dir() / "krx_ohlcv.duckdb"
ENV_PATH = ROOT / ".env"

KRX_BASE = "https://data-dbg.krx.co.kr/svc/apis/sto/"
MARKET_ENDPOINTS = {"KOSPI": "stk_bydd_trd", "KOSDAQ": "ksq_bydd_trd"}
ETF_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/etp/etf_bydd_trd"
REQUEST_SLEEP = 0.1               # KRX rate-limit 완충 (콜 사이)
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3                # 순간 지연 대비 (2026-09-29 08:00 단발 read timeout 사고)
RETRY_WAIT = 30                   # 초. 길게 막히는 점검은 스케줄 재시도(22:00/07:00/08:00/08:30)가 맡는다


def _get_with_retry(http, url: str, **kwargs):
    """시간초과·연결오류·5xx 만 재시도. 4xx(인증 등)는 바로 올린다."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            resp = http.get(url, **kwargs)
            if resp.status_code < 500:
                return resp
            err: Exception = requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
        except (requests.Timeout, requests.ConnectionError) as exc:
            err = exc
        if attempt == RETRY_ATTEMPTS:
            raise err
        print(f"  retry {attempt}/{RETRY_ATTEMPTS - 1} after {RETRY_WAIT}s: {err}")
        time.sleep(RETRY_WAIT)

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
    cmp_prev      INTEGER,  -- KRX CMPPREVDD_PRC 전일대비. 기준가 = close - cmp_prev
    PRIMARY KEY (date, ticker)
)
"""

_CREATE_HOLIDAYS = """
CREATE TABLE IF NOT EXISTS holidays (
    date VARCHAR PRIMARY KEY
)
"""

_CREATE_ETF_OHLCV = """
CREATE TABLE IF NOT EXISTS etf_ohlcv (
    date VARCHAR, ticker VARCHAR, name VARCHAR, idx_name VARCHAR,
    open INTEGER, high INTEGER, low INTEGER, close INTEGER,
    volume BIGINT, trading_value BIGINT, market_cap BIGINT, list_shrs BIGINT,
    nav DOUBLE, net_asset BIGINT,
    PRIMARY KEY (date, ticker)
)
"""


_CREATE_PROVISIONAL = """
CREATE TABLE IF NOT EXISTS ohlcv_provisional (
    date VARCHAR PRIMARY KEY,
    rows INTEGER,
    skipped_corp INTEGER,
    created_at VARCHAR
)
"""

_CREATE_PROVISIONAL_NO1530 = """
CREATE TABLE IF NOT EXISTS ohlcv_provisional_no1530 (
    date VARCHAR,
    ticker VARCHAR,
    PRIMARY KEY (date, ticker)
)
"""

_OHLCV_COLS = (
    "date", "ticker", "market", "open", "high", "low", "close",
    "volume", "trading_value", "market_cap", "list_shrs", "cmp_prev",
)

_COMPARE_COLS = (
    "open", "high", "low", "close", "market", "list_shrs",
    "market_cap", "cmp_prev", "volume", "trading_value",
)
_PRICE_COLS = ("open", "high", "low", "close")


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


def _held_dates_all(con: duckdb.DuckDBPyConnection, from_date: str, to_date: str) -> set[str]:
    """ohlcv 에 1행 이상 있는 날짜 전부 (provisional 포함, held_dates 가 걸러냄)."""
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


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """information_schema 로 표 존재 확인."""
    return (
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [name],
        ).fetchone()[0]
        > 0
    )


def ensure_provisional_table(con: duckdb.DuckDBPyConnection) -> None:
    """분봉 출처 기록 표 2종 생성 (build_krx_from_minute 와 공용)."""
    con.execute(_CREATE_PROVISIONAL)
    con.execute(_CREATE_PROVISIONAL_NO1530)


def held_dates(con: duckdb.DuckDBPyConnection, from_date: str, to_date: str) -> set[str]:
    """캐시에 이미 1행 이상 적재된 거래일 집합 (분봉 출처 날짜는 제외 → KRX 재시도)."""
    held = _held_dates_all(con, from_date, to_date)
    if not _table_exists(con, "ohlcv_provisional"):
        return held
    prov = {
        r[0]
        for r in con.execute(
            "SELECT date FROM ohlcv_provisional WHERE date BETWEEN ? AND ?",
            [from_date, to_date],
        ).fetchall()
    }
    return held - prov


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


def _parse_float(value: str | None) -> float | None:
    """KRX 문자열 숫자 → float. 콤마 제거, 빈값·'-' → None."""
    if value is None:
        return None
    s = value.replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
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
        resp = _get_with_retry(
            http,
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
                    _parse_int(x.get("CMPPREVDD_PRC")),
                )
            )
        time.sleep(REQUEST_SLEEP)
    return rows


def fetch_etf_day(
    date: str,
    key: str,
    session: requests.Session | None = None,
) -> list[tuple]:
    """그날 ETF 전종목 일별시세 → etf_ohlcv 행 튜플 리스트 (거래일당 1콜)."""
    http = session or requests
    headers = {"AUTH_KEY": key}
    resp = _get_with_retry(
        http,
        ETF_ENDPOINT,
        headers=headers,
        params={"basDd": date},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if "OutBlock_1" not in data:
        raise RuntimeError(
            f"KRX unexpected response (ETF {date}): {resp.text[:200]}"
        )
    rows: list[tuple] = []
    for x in data["OutBlock_1"]:
        rows.append(
            (
                x["BAS_DD"],
                x["ISU_CD"],
                str(x.get("ISU_NM", "") or "").strip(),
                str(x.get("IDX_IND_NM", "") or "").strip(),
                _parse_int(x.get("TDD_OPNPRC")),
                _parse_int(x.get("TDD_HGPRC")),
                _parse_int(x.get("TDD_LWPRC")),
                _parse_int(x.get("TDD_CLSPRC")),
                _parse_int(x.get("ACC_TRDVOL")),
                _parse_int(x.get("ACC_TRDVAL")),
                _parse_int(x.get("MKTCAP")),
                _parse_int(x.get("LIST_SHRS")),
                _parse_float(x.get("NAV")),
                _parse_int(x.get("INVSTASST_NETASST_TOTAMT")),
            )
        )
    time.sleep(REQUEST_SLEEP)
    return rows


def _insert_ohlcv_rows(con: duckdb.DuckDBPyConnection, rows: list) -> None:
    """PK 인덱스가 걸린 테이블에 executemany 하면 행마다 인덱스를 갱신해 느리다.
    PK 없는 임시테이블에 모아 넣고 한 번에 upsert 한다(거래일당 10.4초 → 1초 미만)."""
    con.execute("CREATE OR REPLACE TEMP TABLE _ohlcv_staging AS SELECT * FROM ohlcv LIMIT 0")
    con.executemany("INSERT INTO _ohlcv_staging VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO ohlcv SELECT * FROM _ohlcv_staging")


def _insert_etf_ohlcv_rows(con: duckdb.DuckDBPyConnection, rows: list) -> None:
    """etf_ohlcv 일괄 upsert — PK 없는 temp 스테이징 후 일괄."""
    con.execute("CREATE OR REPLACE TEMP TABLE _etf_ohlcv_staging AS SELECT * FROM etf_ohlcv LIMIT 0")
    con.executemany("INSERT INTO _etf_ohlcv_staging VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT OR REPLACE INTO etf_ohlcv SELECT * FROM _etf_ohlcv_staging")


def reconcile_day(
    con: duckdb.DuckDBPyConnection, date: str, rows: list[tuple]
) -> dict | None:
    """분봉 출처 날짜를 KRX 값으로 대조·교정. provisional 날짜가 아니면 None.

    비교 후 그날 ohlcv 행 전부 DELETE → KRX rows INSERT → provisional 기록 삭제.
    결과 행은 KRX 단독 적재와 같다. 한 트랜잭션.
    KRX rows 에 provisional 시장의 일부가 빠졌으면 쓰지 않고
    {"status": "partial", "missing_markets": [...]} 반환.
    """
    if not _table_exists(con, "ohlcv_provisional"):
        return None
    hit = con.execute(
        "SELECT COUNT(*) FROM ohlcv_provisional WHERE date = ?", [date]
    ).fetchone()[0]
    if not hit:
        return None
    cols = ["ticker", *_COMPARE_COLS]
    prov = {}
    for r in con.execute(
        f"SELECT {', '.join(cols)} FROM ohlcv WHERE date = ?", [date]
    ).fetchall():
        prov[r[0]] = dict(zip(_COMPARE_COLS, r[1:]))
    krx = {}
    for row in rows:
        rec = dict(zip(_OHLCV_COLS, row))
        krx[rec["ticker"]] = {c: rec[c] for c in _COMPARE_COLS}
    prov_markets = {v["market"] for v in prov.values()}
    krx_markets = {v["market"] for v in krx.values()}
    missing_markets = sorted(prov_markets - krx_markets)
    if missing_markets:
        return {"status": "partial", "missing_markets": missing_markets}
    if _table_exists(con, "ohlcv_provisional_no1530"):
        no1530 = {
            r[0]
            for r in con.execute(
                "SELECT ticker FROM ohlcv_provisional_no1530 WHERE date = ?", [date]
            ).fetchall()
        }
    else:
        no1530 = set()
    price_changed: list[tuple] = []
    close_changed_1530: list[str] = []
    market_changed: list[tuple] = []
    shares_changed: list[tuple] = []
    cap_changed = 0
    vol_ratios: list[float] = []
    tv_ratios: list[float] = []
    for ticker in sorted(set(prov) | set(krx)):
        if ticker not in prov or ticker not in krx:
            continue
        p, k = prov[ticker], krx[ticker]
        for c in _PRICE_COLS:
            if p[c] != k[c]:
                price_changed.append((ticker, c, p[c], k[c]))
        if p["close"] != k["close"] and ticker not in no1530:
            close_changed_1530.append(ticker)
        if p["market"] != k["market"]:
            market_changed.append((ticker, "market", p["market"], k["market"]))
        if p["list_shrs"] != k["list_shrs"]:
            shares_changed.append((ticker, p["list_shrs"], k["list_shrs"]))
        if p["market_cap"] != k["market_cap"]:
            cap_changed += 1
        if k["volume"] is not None and k["volume"] > 0 and p["volume"] is not None:
            vol_ratios.append(p["volume"] / k["volume"])
        if (
            k["trading_value"] is not None
            and k["trading_value"] > 0
            and p["trading_value"] is not None
        ):
            tv_ratios.append(p["trading_value"] / k["trading_value"])
    rec = {
        "price_changed": price_changed,
        "close_changed_1530": close_changed_1530,
        "market_changed": market_changed,
        "shares_changed": shares_changed,
        "cap_changed": cap_changed,
        "added": sorted(set(krx) - set(prov)),
        "removed": sorted(set(prov) - set(krx)),
        "vol_ratio_median": statistics.median(vol_ratios) if vol_ratios else None,
        "tv_ratio_median": statistics.median(tv_ratios) if tv_ratios else None,
    }
    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM ohlcv WHERE date = ?", [date])
        _insert_ohlcv_rows(con, rows)
        con.execute("DELETE FROM ohlcv_provisional WHERE date = ?", [date])
        if _table_exists(con, "ohlcv_provisional_no1530"):
            con.execute("DELETE FROM ohlcv_provisional_no1530 WHERE date = ?", [date])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return rec


def _fmt_ratio(value) -> str:
    return f"{value:.2f}x" if value is not None else "-"


def _fmt_val(value) -> str:
    return "-" if value is None else str(value)


def format_reconcile(date: str, rec: dict) -> str:
    """대조 결과 알림 문자열 (순수 함수). 첫 줄 ⚠/✅ 요약 + 비율·증감·변경 목록."""
    price_tickers = {entry[0] for entry in rec["price_changed"]}
    n = len(price_tickers)
    k = len(rec["market_changed"])
    j = len(rec["close_changed_1530"])
    a = len(rec["added"])
    r = len(rec["removed"])
    s = len(rec["shares_changed"])
    c = rec["cap_changed"]
    if j >= 1 or k >= 1 or n > 30:
        first = (
            f"⚠ [KRX OHLCV] {date} 분봉 일봉 교정 — "
            f"가격 {n}종목·시장 {k}종목 변경 (15:30 봉 종가 변경 {j})"
        )
        emphasis = True
    else:
        close_all = {entry[0] for entry in rec["price_changed"] if entry[1] == "close"}
        n1 = len(close_all - set(rec["close_changed_1530"]))
        n2 = n - n1
        first = (
            f"✅ [KRX OHLCV] {date} KRX 대조 — "
            f"종가 {n1}종목(15:30 봉 없음)·그 밖 가격 {n2}종목·추가 {a}·삭제 {r}, 평소 범위"
        )
        emphasis = False
    lines = [
        first,
        f"- 거래량 비율 중앙 {_fmt_ratio(rec['vol_ratio_median'])} / 거래대금 {_fmt_ratio(rec['tv_ratio_median'])}",
        f"- 추가 {a} · 삭제 {r}",
        f"- 주식수 변경 {s}종목(평소 범위)",
        f"- 시총 교정 {c}종목",
    ]
    listed = 0
    for ticker, prov_v, krx_v in rec["shares_changed"]:
        if listed >= 10:
            break
        if prov_v is None or krx_v is None or prov_v <= 0:
            continue
        ratio = krx_v / prov_v
        if abs(ratio - 1) >= 0.05:
            lines.append(f"  주식수 {ticker} {prov_v}→{krx_v} ({(ratio - 1) * 100:+.1f}%)")
            listed += 1
    if emphasis:
        by_ticker: dict[str, list[tuple]] = {}
        for entry in rec["price_changed"]:
            by_ticker.setdefault(entry[0], []).append(entry)
        ordered: list[tuple] = []
        for t in rec["close_changed_1530"]:
            if t in by_ticker:
                ordered.extend(by_ticker.pop(t))
        ordered.extend(rec["market_changed"])
        for entries in by_ticker.values():
            ordered.extend(entries)
        for ticker, col, prov_v, krx_v in ordered[:20]:
            lines.append(f"{ticker} {col} {_fmt_val(prov_v)}→{_fmt_val(krx_v)}")
    return "\n".join(lines)


def ensure_etf_ohlcv(
    con: duckdb.DuckDBPyConnection,
    from_date: str,
    to_date: str,
    key: str,
    force: bool = False,
) -> dict:
    """주식 ohlcv 에 행이 있는 날짜만 ETF 일별시세로 채운다.

    force=True 면 etf_ohlcv 에 이미 있는 날짜도 다시 받아 덮어쓴다.
    날짜 단위 오류는 해당 날짜만 건너뛰고 계속 진행(다음 실행에서 재시도).

    Returns: 통계 dict (missing_days/fetched_days/inserted_rows/failed_days).
    """
    con.execute(_CREATE_ETF_OHLCV)
    targets = [
        r[0] for r in con.execute(
            "SELECT DISTINCT date FROM ohlcv WHERE date BETWEEN ? AND ? ORDER BY date",
            [from_date, to_date],
        ).fetchall()
    ]
    if not force:
        held = {
            r[0] for r in con.execute(
                "SELECT DISTINCT date FROM etf_ohlcv WHERE date BETWEEN ? AND ?",
                [from_date, to_date],
            ).fetchall()
        }
        targets = [d for d in targets if d not in held]

    session = requests.Session()
    fetched_days = 0
    inserted_rows = 0
    failed_days = 0
    for date in targets:
        try:
            rows = fetch_etf_day(date, key, session=session)
            if rows:
                _insert_etf_ohlcv_rows(con, rows)
                fetched_days += 1
                inserted_rows += len(rows)
                print(f"  {date}: {len(rows)} etf rows")
        except Exception as exc:
            print(f"  {date}: etf fetch/insert failed - {exc} (retry next run)")
            failed_days += 1
            continue

    return {
        "missing_days": len(targets),
        "fetched_days": fetched_days,
        "inserted_rows": inserted_rows,
        "failed_days": failed_days,
    }


def run_etf_step(
    con: duckdb.DuckDBPyConnection,
    from_date: str,
    to_date: str,
    key: str,
    force: bool = False,
) -> str:
    """ETF 일봉 단계를 주식 결과와 분리 — 예외를 문자열로만 반환한다."""
    try:
        stats = ensure_etf_ohlcv(con, from_date, to_date, key, force=force)
    except Exception as exc:
        return f"etf_error={str(exc)[:200]}"
    return f"etf=fetched {stats['fetched_days']}d/{stats['inserted_rows']}rows failed={stats['failed_days']}"


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
             inserted_rows/holiday_days/failed_days/reconciled).
    """
    con.execute(_CREATE_OHLCV)
    con.execute(_CREATE_HOLIDAYS)
    ensure_provisional_table(con)
    # 기존 DB 마이그레이션 — 새 컬럼 없으면 추가
    existing_cols = {r[1] for r in con.execute("PRAGMA table_info('ohlcv')").fetchall()}
    for col, dtype in [("market_cap", "BIGINT"), ("list_shrs", "BIGINT"), ("cmp_prev", "INTEGER")]:
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
    reconciled: dict[str, dict] = {}
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
        rec = reconcile_day(con, date, rows)
        if rec is not None and rec.get("status") == "partial":
            missing = ",".join(rec["missing_markets"])
            print(f"  {date}: KRX partial ({missing}) - keep minute rows (retry next run)")
            failed_days += 1
            continue
        if rec is None:
            _insert_ohlcv_rows(con, rows)
        else:
            reconciled[date] = rec
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
    if _table_exists(con, "ohlcv_provisional"):
        max_data_date = con.execute(
            "SELECT max(date) FROM ohlcv WHERE date NOT IN (SELECT date FROM ohlcv_provisional)"
        ).fetchone()[0]
    else:
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
        "reconciled": reconciled,
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
        etf_str = run_etf_step(con, from_date, to_date, key, args.force)
    finally:
        con.close()

    print(
        f"[{from_date}~{to_date}] calendar={stats['calendar_days']} "
        f"held={stats['held_days']} missing={stats['missing_days']} "
        f"fetched={stats['fetched_days']}d/{stats['inserted_rows']}rows "
        f"holidays+{stats['holiday_days']} failed={stats['failed_days']} "
        f"→ {args.db_path} {etf_str}"
    )
    for _rec_date in sorted(stats.get("reconciled", {})):
        print("RECONCILE_BEGIN")
        print(format_reconcile(_rec_date, stats["reconciled"][_rec_date]))
        print("RECONCILE_END")


if __name__ == "__main__":
    main()
