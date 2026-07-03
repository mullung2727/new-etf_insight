"""종목명 ↔ 종목코드 매핑 캐시 (krx_ohlcv.duckdb / stock_names 테이블).

KRX 일별 시세 응답(stk_bydd_trd/ksq_bydd_trd)은 ISU_CD(6자리 코드)+ISU_NM(종목명)을
전종목 내려준다. build_krx_ohlcv 는 코드만 쓰고 이름을 버리므로, 여기서 같은 응답의
이름을 별도 테이블에 persist 한다 — 전종목 name→code 조회용.

소비자: 텔레그램 증권사 리포트 다운로더(`[기업][종목명]` → 코드 → 폴더명),
그리고 watchlist 계열(다음 단계에서 이 맵을 쓰도록 이관).

Usage (from etl/):
    uv run python scripts/stock_names.py            # ohlcv 최신 거래일 기준 갱신
    uv run python scripts/stock_names.py --date 20260701
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import duckdb
import requests

try:
    from scripts.build_krx_ohlcv import (
        DEFAULT_DB_PATH,
        KRX_BASE,
        MARKET_ENDPOINTS,
        REQUEST_SLEEP,
        REQUEST_TIMEOUT,
        load_api_key,
    )
except ImportError:
    from build_krx_ohlcv import (
        DEFAULT_DB_PATH,
        KRX_BASE,
        MARKET_ENDPOINTS,
        REQUEST_SLEEP,
        REQUEST_TIMEOUT,
        load_api_key,
    )


_CREATE_STOCK_NAMES = """
CREATE TABLE IF NOT EXISTS stock_names (
    code       VARCHAR PRIMARY KEY,
    name       VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL
)
"""


def ensure_table(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_CREATE_STOCK_NAMES)


def fetch_names(date: str, key: str, session: requests.Session | None = None) -> list[tuple[str, str]]:
    """그날 KOSPI+KOSDAQ 전종목 (code, name) 목록 (거래일당 2콜).

    코드/이름 중 하나라도 빈 행은 건너뛴다. 휴장일 등 빈 응답은 [] 로 위장하지 않고
    OutBlock_1 키 자체가 없으면 에러(build_krx_ohlcv.fetch_day 와 동일 방침).
    """
    http = session or requests
    pairs: list[tuple[str, str]] = []
    headers = {"AUTH_KEY": key}
    for _market, endpoint in MARKET_ENDPOINTS.items():
        resp = http.get(
            KRX_BASE + endpoint,
            headers=headers,
            params={"basDd": date},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "OutBlock_1" not in data:
            raise RuntimeError(f"KRX unexpected response ({_market} {date}): {resp.text[:200]}")
        for x in data["OutBlock_1"]:
            code = str(x.get("ISU_CD", "") or "").strip()
            name = str(x.get("ISU_NM", "") or "").strip()
            if code and name:
                pairs.append((code, name))
        time.sleep(REQUEST_SLEEP)
    return pairs


def upsert_names(con: duckdb.DuckDBPyConnection, pairs: list[tuple[str, str]]) -> int:
    ensure_table(con)
    now = datetime.now(timezone.utc).isoformat()
    con.executemany(
        "INSERT OR REPLACE INTO stock_names VALUES (?,?,?)",
        [(code, name, now) for code, name in pairs],
    )
    return len(pairs)


def load_name_to_code(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """종목명 → 코드. 동일 이름이 여러 코드면 ORDER BY code 로 결정론 보장
    (마지막=가장 큰 code 가 이김). 재현성 위해 정렬 필수."""
    rows = con.execute("SELECT code, name FROM stock_names ORDER BY code").fetchall()
    return {name: code for code, name in rows}


def load_code_to_name(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    rows = con.execute("SELECT code, name FROM stock_names ORDER BY code").fetchall()
    return {code: name for code, name in rows}


def build(date: str | None = None, db_path: Path = DEFAULT_DB_PATH, key: str | None = None) -> dict:
    """KRX 이름을 받아 stock_names 갱신. date 미지정 시 ohlcv 최신 거래일 사용."""
    key = key or load_api_key()
    con = duckdb.connect(str(db_path))
    try:
        ensure_table(con)
        if date is None:
            row = con.execute("SELECT max(date) FROM ohlcv").fetchone()
            date = row[0] if row else None
            if not date:
                raise RuntimeError("ohlcv 비어있음 — --date 로 거래일 지정 필요")
        pairs = fetch_names(date, key)
        upsert_names(con, pairs)
        total = con.execute("SELECT COUNT(*) FROM stock_names").fetchone()[0]
        return {"date": date, "fetched": len(pairs), "total": total}
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh stock_names (code↔name) from KRX")
    parser.add_argument("--date", help="기준 거래일 YYYYMMDD (기본: ohlcv 최신 거래일)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()
    stats = build(date=args.date, db_path=args.db)
    print(f"stock_names: date={stats['date']} fetched={stats['fetched']} total={stats['total']}")


if __name__ == "__main__":
    main()
