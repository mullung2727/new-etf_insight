"""전 상장사 DART 주요재무지표(fnlttCmpnyIndx) 적재 배치.

목표: 전 종목 ROE 등 재무지표를 sqlite(db/financial_indicators.sqlite3)에 쌓아
'전체 종목 ROE 높은 순' 같은 랭킹을 DB 쿼리로 뽑는다.

DART 지표 API는 시장 전체 스캔이 없고 corp_code(콤마 다중 가능) 필수, 2023 사업연도부터
데이터 제공. 카테고리 4개 = 종목당 66지표:
  M210000 수익성(ROE=M211550) / M220000 안정성(부채비율=M221100)
  M230000 성장성(매출증가율=M231000) / M240000 활동성

구조: 순수 다중호출 core(fetch_indicators_chunk) + 래퍼 러너(run, 다음 단계).

Usage (from etl/):
    uv run python scripts/build_financial_indicators.py        # 1단계 self-check
"""
from __future__ import annotations

import argparse
import io
import sys
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path: etl/·scripts/·src/)

import requests  # noqa: E402

from new_etf_insight.dart_client import fetch_dart_list, get_api_key  # noqa: E402
from wl_sqlite import connect_rw  # noqa: E402

BASE_URL = "https://opendart.fss.or.kr/api"
INDX_API_URL = f"{BASE_URL}/fnlttCmpnyIndx.json"
ACNT_API_URL = f"{BASE_URL}/fnlttMultiAcnt.json"
CORPCODE_API_URL = f"{BASE_URL}/corpCode.xml"

# 지표 카테고리 4개 (종목당 66지표). 원본 전량 저장 대상.
IDX_CATEGORIES = ["M210000", "M220000", "M230000", "M240000"]

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "financial_indicators.sqlite3"
# 현재 상장 종목코드(KRX). corpCode.xml stock_code는 상폐사도 유지하므로 이걸로 교차 필터.
KRX_OHLCV_DB = Path(__file__).resolve().parents[1] / "db" / "krx_ohlcv.duckdb"

BATCH = 10       # corp_code / 호출 (콤마 다중)
DELAY = 0.1      # sec / 호출
REQUEST_TIMEOUT = 30


def fetch_indicators_chunk(
    corp_codes: list[str],
    year: str,
    reprt: str,
    idx_cl_code: str,
    key: str,
    session: requests.Session | None = None,
) -> list[dict]:
    """corp_code 묶음(콤마 다중) 1콜 → 지표 rows(원본 그대로) 반환.

    순수: DB·sleep 안 함. status 000이면 list, 013(무자료)·기타는 [].
    year·reprt를 인자로 받으므로 분기 확장 시 그대로 재사용.
    """
    return fetch_dart_list(
        INDX_API_URL,
        {
            "corp_code": ",".join(corp_codes),
            "bsns_year": year,
            "reprt_code": reprt,
            "idx_cl_code": idx_cl_code,
        },
        key,
        session=session,
        timeout=REQUEST_TIMEOUT,
    )


def fetch_accounts_chunk(
    corp_codes: list[str],
    year: str,
    reprt: str,
    key: str,
    session: requests.Session | None = None,
) -> list[dict]:
    """corp_code 묶음 → 주요계정(금액 14개, CFS+OFS) rows(원본 그대로).

    지표와 달리 카테고리 루프 없이 1콜로 전 계정. status 013/기타는 [].
    """
    return fetch_dart_list(
        ACNT_API_URL,
        {
            "corp_code": ",".join(corp_codes),
            "bsns_year": year,
            "reprt_code": reprt,
        },
        key,
        session=session,
        timeout=REQUEST_TIMEOUT,
    )


# ── DB ────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corps (
    corp_code  TEXT PRIMARY KEY,
    stock_code TEXT NOT NULL,
    corp_name  TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS indicators (
    corp_code   TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    idx_cl_code TEXT NOT NULL,
    idx_code    TEXT NOT NULL,
    idx_nm      TEXT,
    idx_val     REAL,
    stock_code  TEXT,
    stlm_dt     TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, idx_code)
);
CREATE INDEX IF NOT EXISTS ix_ind_rank ON indicators(idx_code, bsns_year, reprt_code, idx_val);
CREATE TABLE IF NOT EXISTS accounts (
    corp_code   TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    fs_div      TEXT NOT NULL,       -- CFS 연결 / OFS 별도
    sj_div      TEXT,                -- BS / IS
    account_nm  TEXT NOT NULL,       -- 매출액·영업이익·자산총계 등 (주요계정은 account_id 없음)
    amount      REAL,                -- thstrm_amount
    stock_code  TEXT,
    currency    TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year, reprt_code, fs_div, account_nm)
);
CREATE INDEX IF NOT EXISTS ix_acnt_rank ON accounts(account_nm, bsns_year, reprt_code, fs_div, amount);
CREATE VIEW IF NOT EXISTS v_key_indicators AS
SELECT i.corp_code, c.corp_name, i.stock_code, i.bsns_year, i.reprt_code,
       MAX(CASE WHEN i.idx_code='M211550' THEN i.idx_val END) AS roe,
       MAX(CASE WHEN i.idx_code='M221100' THEN i.idx_val END) AS debt_ratio,
       MAX(CASE WHEN i.idx_code='M231000' THEN i.idx_val END) AS revenue_growth,
       MAX(CASE WHEN i.idx_code='M211200' THEN i.idx_val END) AS net_margin
FROM indicators i JOIN corps c USING(corp_code)
GROUP BY i.corp_code, i.bsns_year, i.reprt_code;
-- 금액 랭킹 VIEW: CFS 우선(없으면 OFS는 별도), 영업이익률 계산 포함
CREATE VIEW IF NOT EXISTS v_key_accounts AS
SELECT a.corp_code, c.corp_name, a.stock_code, a.bsns_year, a.reprt_code,
       MAX(CASE WHEN a.account_nm='매출액' THEN a.amount END) AS revenue,
       MAX(CASE WHEN a.account_nm='영업이익' THEN a.amount END) AS op_profit,
       MAX(CASE WHEN a.account_nm='당기순이익(손실)' THEN a.amount END) AS net_income,
       MAX(CASE WHEN a.account_nm='자산총계' THEN a.amount END) AS total_assets,
       CASE WHEN MAX(CASE WHEN a.account_nm='매출액' THEN a.amount END) > 0
            THEN round(100.0 * MAX(CASE WHEN a.account_nm='영업이익' THEN a.amount END)
                             / MAX(CASE WHEN a.account_nm='매출액' THEN a.amount END), 2)
       END AS op_margin
FROM accounts a JOIN corps c USING(corp_code)
WHERE a.fs_div='CFS'
GROUP BY a.corp_code, a.bsns_year, a.reprt_code;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def ensure_schema(con) -> None:
    con.executescript(_SCHEMA)


def upsert_corps(con, corps: list[tuple[str, str, str]]) -> None:
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO corps VALUES (?,?,?,?)",
        [(cc, sc, nm, now) for cc, sc, nm in corps],
    )


def upsert_indicators(con, rows: list[dict]) -> int:
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO indicators VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r["corp_code"], r["bsns_year"], r["reprt_code"],
                r.get("idx_cl_code"), r["idx_code"], r.get("idx_nm"),
                _to_float(r.get("idx_val")), r.get("stock_code"), r.get("stlm_dt"),
                now,
            )
            for r in rows
        ],
    )
    return len(rows)


def upsert_accounts(con, rows: list[dict]) -> int:
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                r["corp_code"], r["bsns_year"], r["reprt_code"],
                r["fs_div"], r.get("sj_div"), r["account_nm"],
                _to_float(r.get("thstrm_amount")), r.get("stock_code"), r.get("currency"),
                now,
            )
            for r in rows
        ],
    )
    return len(rows)


def existing_corps(con, table: str, year: str, reprt: str) -> set[str]:
    """해당 table의 period(bsns_year+reprt_code)를 이미 보유한 corp_code 집합.
    table은 코드 내부 상수(indicators/accounts)만 전달 — SQL 인젝션 무관."""
    cur = con.execute(
        f"SELECT DISTINCT corp_code FROM {table} WHERE bsns_year=? AND reprt_code=?",
        (year, reprt),
    )
    return {row[0] for row in cur}


# ── 유니버스 (corpCode.xml) ─────────────────────────────────────────────────────

def fetch_listed_corps(key: str) -> list[tuple[str, str, str]]:
    """corpCode.xml → 상장사(stock_code 있는 것)만 (corp_code, stock_code, corp_name)."""
    resp = requests.get(CORPCODE_API_URL, params={"crtfc_key": key}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_name = next(n for n in zf.namelist() if n.endswith(".xml"))
        xml = zf.read(xml_name)
    root = ET.fromstring(xml)
    out: list[tuple[str, str, str]] = []
    for el in root.iter("list"):
        stock = (el.findtext("stock_code") or "").strip()
        if not stock:
            continue
        corp = (el.findtext("corp_code") or "").strip()
        name = (el.findtext("corp_name") or "").strip()
        out.append((corp, stock, name))
    return out


def load_krx_listed_codes(db_path: Path = KRX_OHLCV_DB) -> set[str] | None:
    """KRX 현재상장 종목코드 집합(stock_names). 없으면 None(필터 생략, 폴백)."""
    if not db_path.exists():
        return None
    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {row[0] for row in con.execute("SELECT code FROM stock_names").fetchall()}
    except duckdb.Error:
        return None
    finally:
        con.close()


# ── period(연도) 결정 ───────────────────────────────────────────────────────────

def latest_annual_year(key: str, probe_corp: str = "00126380") -> str:
    """삼성 기준 current-1부터 내림차순 probe해 최신 가용 연간 사업연도."""
    y = date.today().year - 1
    for _ in range(4):
        if fetch_indicators_chunk([probe_corp], str(y), "11011", "M210000", key):
            return str(y)
        y -= 1
    raise RuntimeError("최신 가용 연간 사업연도를 찾지 못함")


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ── 래퍼 러너 ───────────────────────────────────────────────────────────────────

def _fetch_indicator_rows(chunk, year, reprt, key, session) -> list[dict]:
    """청크당 4카테고리 순회. 카테고리마다 sleep."""
    buf: list[dict] = []
    for cat in IDX_CATEGORIES:
        buf += fetch_indicators_chunk(chunk, year, reprt, cat, key, session)
        time.sleep(DELAY)
    return buf


def _fetch_account_rows(chunk, year, reprt, key, session) -> list[dict]:
    """청크당 1콜(주요계정은 카테고리 없음)."""
    rows = fetch_accounts_chunk(chunk, year, reprt, key, session)
    time.sleep(DELAY)
    return rows


# source 이름 → (table, fetch_fn, upsert_fn). skip/chunk 루프는 공유.
SOURCES = {
    "indicators": ("indicators", _fetch_indicator_rows, upsert_indicators),
    "accounts": ("accounts", _fetch_account_rows, upsert_accounts),
}


def _process_source(con, source, universe, year, reprt, limit, force, key, session) -> dict:
    """한 source × 한 period 적재. skip→chunk 호출→upsert→chunk commit."""
    table, fetch_fn, upsert_fn = SOURCES[source]
    done = set() if force else existing_corps(con, table, year, reprt)
    targets = [cc for cc in universe if cc not in done]
    if limit is not None:
        targets = targets[:limit]
    chunks = list(_chunks(targets, BATCH))
    print(f"[{source} {year} {reprt}] 대상 {len(targets)} (skip {len(universe) - len(targets)}), chunk {len(chunks)}")

    prows = 0
    for ci, chunk in enumerate(chunks, 1):
        rows = fetch_fn(chunk, year, reprt, key, session)
        prows += upsert_fn(con, rows)
        con.commit()  # chunk 단위 보존 → 중단 시 재실행 이어받기
        print(f"[{source} {year} {reprt}][chunk {ci}/{len(chunks)}] corps={len(chunk)} rows={len(rows)}")
    return {"source": source, "year": year, "reprt": reprt, "targets": len(targets), "rows": prows}


def run(
    db_path: Path = DEFAULT_DB_PATH,
    periods: list[tuple[str, str]] | None = None,
    sources: list[str] | None = None,
    limit: int | None = None,
    force: bool = False,
    key: str | None = None,
) -> dict:
    """source × period 격자 적재. period=(bsns_year,reprt_code), source∈{indicators,accounts}."""
    key = key or get_api_key()
    sources = sources or list(SOURCES)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    stats = {"universe": 0, "runs": [], "rows": 0}
    with connect_rw(db_path) as con:
        ensure_schema(con)

        corps = fetch_listed_corps(key)
        upsert_corps(con, corps)  # 이름 조회용으로 전량 저장(상폐 포함, 무해)
        con.commit()
        # 현재상장(KRX)만 타깃 — corpCode.xml stock_code엔 상폐사도 남아 013 유발
        krx = load_krx_listed_codes()
        if krx is None:
            print("경고: KRX 상장코드 없음 → corpCode.xml 전량 대상(상폐 포함)")
            universe = [cc for cc, _sc, _nm in corps]
        else:
            universe = [cc for cc, sc, _nm in corps if sc in krx]
        stats["universe"] = len(universe)
        print(f"유니버스 상장사: {len(universe)} (corpCode {len(corps)} / KRX 현재상장 교차)")

        if periods is None:
            periods = [(latest_annual_year(key), "11011")]

        for year, reprt in periods:
            for source in sources:
                r = _process_source(con, source, universe, year, reprt, limit, force, key, session)
                stats["runs"].append(r)
                stats["rows"] += r["rows"]

    return stats


def _self_check() -> None:
    """1단계 self-check: 삼성·SK하이닉스·현대차 실호출로 core 검증."""
    key = get_api_key()
    corps = {"00126380": "삼성전자", "00164779": "SK하이닉스", "00164742": "현대차"}
    rows = fetch_indicators_chunk(list(corps), "2024", "11011", "M210000", key)

    assert rows, "수익성(M210000) rows 비어있음"
    got = {r["corp_code"] for r in rows}
    assert got == set(corps), f"corp_code 불일치: 기대 {set(corps)}, 실제 {got}"

    roe = {r["corp_code"]: r.get("idx_val") for r in rows if r.get("idx_code") == "M211550"}
    assert all(roe.values()), f"ROE 결측: {roe}"
    # 앞서 실측한 삼성 2024 ROE=8.997 대조
    assert abs(float(roe["00126380"]) - 8.997) < 0.01, f"삼성 ROE 틀림: {roe['00126380']}"

    # 무자료 처리: 존재하지 않는 corp_code → []
    assert fetch_indicators_chunk(["99999999"], "2024", "11011", "M210000", key) == [], \
        "무자료가 []가 아님"

    # 금액 core (fnlttMultiAcnt)
    acnt = fetch_accounts_chunk(list(corps), "2024", "11011", key)
    assert acnt, "주요계정 rows 비어있음"
    assert {r["corp_code"] for r in acnt} == set(corps), "계정 corp_code 불일치"
    # 삼성 영업이익률 = 영업이익/매출액 (CFS). 실측 매출 300.87조/영업이익 32.73조 ≈ 10.9%
    def amt(cc, nm):
        r = next(x for x in acnt if x["corp_code"] == cc and x["account_nm"] == nm and x["fs_div"] == "CFS")
        return _to_float(r["thstrm_amount"])
    op_margin = 100 * amt("00126380", "영업이익") / amt("00126380", "매출액")
    assert 10 < op_margin < 12, f"삼성 영업이익률 이상: {op_margin}"

    print("core OK — 지표 rows:", len(rows), "계정 rows:", len(acnt), "corps:", len(got))
    for cc, nm in corps.items():
        print(f"  {cc} {nm} ROE={roe[cc]}")
    print(f"삼성 영업이익률(계산)={op_margin:.2f}%")
    print("무자료 corp_code → [] 확인")
    print("self-check PASS")


def main() -> None:
    p = argparse.ArgumentParser(description="전 상장사 DART 재무지표 적재")
    p.add_argument("--self-check", action="store_true", help="core 함수 실호출 검증만")
    p.add_argument("--limit", type=int, default=None, help="남은 대상 앞 N종목만")
    p.add_argument("--year", help="사업연도 강제 YYYY (기본: 최신 가용 연간)")
    p.add_argument("--reprt", default="11011",
                   help="보고서: 11011 연간 / 11013 1Q / 11012 반기 / 11014 3Q (--year 필요)")
    p.add_argument("--source", choices=[*SOURCES, "both"], default="both",
                   help="indicators(지표) / accounts(금액) / both(기본)")
    p.add_argument("--force", action="store_true", help="skip 무시 전량 재적재")
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return

    periods = [(args.year, args.reprt)] if args.year else None
    sources = list(SOURCES) if args.source == "both" else [args.source]
    stats = run(db_path=args.db, periods=periods, sources=sources, limit=args.limit, force=args.force)
    print("DONE", stats)


if __name__ == "__main__":
    main()
