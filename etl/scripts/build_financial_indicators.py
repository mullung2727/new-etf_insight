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
import hashlib
import io
import json
import re
import sqlite3
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path: etl/·scripts/·src/)

import requests  # noqa: E402

from new_etf_insight.dart_client import DartAPIError, fetch_all_filings, fetch_dart_list, get_api_key  # noqa: E402
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
# corpCode.xml zip 로컬 캐시 (신규상장/상폐로만 변동 → 하루 1회 갱신 충분)
CORPCODE_CACHE = Path(__file__).resolve().parents[1] / "db" / "corpcode.zip"
CORPCODE_TTL_SEC = 24 * 3600

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

    순수: DB·sleep 안 함. status 000이면 list, 013(무자료)만 [].
    014·020·인증 오류·기타 비-000과 000인데 list 누락(malformed)은 DartAPIError.
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

    지표와 달리 카테고리 루프 없이 1콜로 전 계정. 013만 [], 그 외 비-000·malformed는 DartAPIError.
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

# §12 확정 DDL. IF NOT EXISTS만 덧붙여 멱등으로 적용한다.
_HISTORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS financial_response_history (
    history_id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    corp_code TEXT NOT NULL,
    bsns_year TEXT NOT NULL,
    reprt_code TEXT NOT NULL,
    idx_cl_code TEXT NOT NULL DEFAULT '',
    rcept_no TEXT,
    rcept_dt TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    first_payload_collected_at TEXT,
    last_observed_at TEXT NOT NULL
);
"""

_HISTORY_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_financial_response_history ON financial_response_history(
    source, corp_code, bsns_year, reprt_code, idx_cl_code,
    COALESCE(rcept_no, ''), payload_hash
) WHERE source <> 'indicators';
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


def _stable_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _exec_schema_statements(con, script: str) -> None:
    """executescript 없이 개별 execute. implicit commit 없이 호출자 transaction을 유지한다."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            if stmt.strip(";\n\t ") == "":
                continue
            con.execute(stmt)
    rest = buf.strip()
    if rest:
        con.execute(rest)


def _table_columns(con, table: str) -> set[str]:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _ensure_column(con, table: str, column: str, coltype: str) -> None:
    if column not in _table_columns(con, table):
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _redact_sensitive(obj):
    if isinstance(obj, dict):
        return {
            k: ("<redacted>" if str(k).lower() in ("crtfc_key", "api_key", "apikey", "key")
                else _redact_sensitive(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [_redact_sensitive(v) for v in obj]
    return obj


def _is_older_receipt(incoming: str, existing: str) -> bool:
    """둘 다 알려진 번호일 때 incoming이 더 오래됐으면 True. 14자리 숫자는 lexical 비교."""
    if incoming == existing:
        return False
    return incoming < existing


def _snapshot_legacy(con, now: str) -> None:
    """history 표 신규 생성 시에만 호출. 기존 current 값을 legacy_* 행으로 보존. current 불변."""
    for corp, year, reprt in con.execute(
        "SELECT DISTINCT corp_code, bsns_year, reprt_code FROM accounts"
    ).fetchall():
        rows = con.execute(
            "SELECT corp_code, bsns_year, reprt_code, fs_div, sj_div, account_nm,"
            " amount, stock_code, currency, updated_at FROM accounts"
            " WHERE corp_code=? AND bsns_year=? AND reprt_code=?"
            " ORDER BY fs_div, sj_div, account_nm",
            (corp, year, reprt),
        ).fetchall()
        payload = [
            {"corp_code": r[0], "bsns_year": r[1], "reprt_code": r[2], "fs_div": r[3],
             "sj_div": r[4], "account_nm": r[5], "amount": r[6], "stock_code": r[7],
             "currency": r[8], "updated_at": r[9]}
            for r in rows
        ]
        pjson = _stable_json(payload)
        con.execute(
            "INSERT INTO financial_response_history (source, corp_code, bsns_year, reprt_code,"
            " idx_cl_code, rcept_no, rcept_dt, payload_hash, payload_json, provenance_json,"
            " first_payload_collected_at, last_observed_at)"
            " VALUES ('legacy_accounts',?,?,?,?,?,?,?,?,?,?,?)",
            (corp, year, reprt, "", None, None, _payload_hash(pjson), pjson,
             _stable_json({"legacy": True}), None, now),
        )
    for corp, year, reprt, cat in con.execute(
        "SELECT DISTINCT corp_code, bsns_year, reprt_code, idx_cl_code FROM indicators"
    ).fetchall():
        rows = con.execute(
            "SELECT corp_code, bsns_year, reprt_code, idx_cl_code, idx_code, idx_nm,"
            " idx_val, stock_code, stlm_dt, updated_at FROM indicators"
            " WHERE corp_code=? AND bsns_year=? AND reprt_code=?"
            " AND COALESCE(idx_cl_code,'')=COALESCE(?,'')"
            " ORDER BY idx_code",
            (corp, year, reprt, cat),
        ).fetchall()
        payload = [
            {"corp_code": r[0], "bsns_year": r[1], "reprt_code": r[2], "idx_cl_code": r[3],
             "idx_code": r[4], "idx_nm": r[5], "idx_val": r[6], "stock_code": r[7],
             "stlm_dt": r[8], "updated_at": r[9]}
            for r in rows
        ]
        pjson = _stable_json(payload)
        con.execute(
            "INSERT INTO financial_response_history (source, corp_code, bsns_year, reprt_code,"
            " idx_cl_code, rcept_no, rcept_dt, payload_hash, payload_json, provenance_json,"
            " first_payload_collected_at, last_observed_at)"
            " VALUES ('legacy_indicators',?,?,?,?,?,?,?,?,?,?,?)",
            (corp, year, reprt, cat or "", None, None, _payload_hash(pjson), pjson,
             _stable_json({"legacy": True}), None, now),
        )


def ensure_schema(con) -> None:
    """기존 스키마 + §12 이력 표·current 출처 컬럼 + legacy snapshot. 전체 원자적.

    executescript를 쓰지 않아 implicit commit이 없고, 실패하면 전체 rollback된다.
    history 표가 새로 생긴 경우에만 legacy snapshot을 같은 transaction에서 적재한다.
    재실행은 snapshot을 만들지 않으므로(표 존재) 바뀐 current가 새 legacy가 되지 않는다.
    commit은 호출자가 유지한다.
    """
    try:
        _exec_schema_statements(con, _SCHEMA)
        existed = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name='financial_response_history'"
        ).fetchone() is not None
        _exec_schema_statements(con, _HISTORY_TABLE_SQL)
        _exec_schema_statements(con, _HISTORY_INDEX_SQL)
        _ensure_column(con, "accounts", "rcept_no", "TEXT")
        _ensure_column(con, "accounts", "first_payload_collected_at", "TEXT")
        _ensure_column(con, "accounts", "history_id", "INTEGER")
        _ensure_column(con, "indicators", "first_payload_collected_at", "TEXT")
        _ensure_column(con, "indicators", "history_id", "INTEGER")
        if not existed:
            _snapshot_legacy(con, _now())
    except Exception:
        con.rollback()
        raise


def upsert_corps(con, corps: list[tuple[str, str, str]]) -> None:
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO corps VALUES (?,?,?,?)",
        [(cc, sc, nm, now) for cc, sc, nm in corps],
    )


def upsert_indicators(
    con,
    rows: list[dict],
    request_context: dict | None = None,
    filings_by_rcept: dict | None = None,
) -> int:
    """지표 current 갱신 + 지표 관측 이력 보존. 같은 savepoint라 current 실패 시 이력도 rollback.

    회사·연도·보고서·분류별 최신 관측과 내용 비교: 같으면 last만 갱신, 다르면 새 행(A→B→A=3행).
    지표에 접수번호를 붙이지 않는다(filings_by_rcept 무시, matched None).
    commit은 호출자가 유지한다.
    """
    if not rows:
        return 0
    now = _now()
    req = _redact_sensitive(dict(request_context or {}))
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(
            (r["corp_code"], r["bsns_year"], r["reprt_code"], r.get("idx_cl_code") or ""),
            [],
        ).append(r)
    con.execute("SAVEPOINT upsert_indicators")
    try:
        written = 0
        for (corp, year, reprt, cat), grows in groups.items():
            ordered = sorted(grows, key=lambda r: str(r.get("idx_code") or ""))
            pjson = _stable_json(ordered)
            phash = _payload_hash(pjson)
            prev = con.execute(
                "SELECT history_id, payload_hash, first_payload_collected_at"
                " FROM financial_response_history"
                " WHERE source='indicators' AND corp_code=? AND bsns_year=?"
                " AND reprt_code=? AND idx_cl_code=? ORDER BY history_id DESC LIMIT 1",
                (corp, year, reprt, cat),
            ).fetchone()
            if prev is not None and prev[1] == phash:
                hid = prev[0]
                first_collected = prev[2] or now
                con.execute(
                    "UPDATE financial_response_history SET last_observed_at=? WHERE history_id=?",
                    (now, hid),
                )
            else:
                cur = con.execute(
                    "INSERT INTO financial_response_history (source, corp_code, bsns_year, reprt_code,"
                    " idx_cl_code, rcept_no, rcept_dt, payload_hash, payload_json, provenance_json,"
                    " first_payload_collected_at, last_observed_at)"
                    " VALUES ('indicators',?,?,?,?,?,?,?,?,?,?,?)",
                    (corp, year, reprt, cat, None, None, phash, pjson,
                     _stable_json({"request": req, "matched_filing": None}), now, now),
                )
                hid = cur.lastrowid
                first_collected = now
            seen: dict[tuple, dict] = {}
            for r in grows:
                seen[(r["corp_code"], r["bsns_year"], r["reprt_code"], r["idx_code"])] = r
            for r in seen.values():
                con.execute(
                    "INSERT INTO indicators (corp_code, bsns_year, reprt_code, idx_cl_code, idx_code,"
                    " idx_nm, idx_val, stock_code, stlm_dt, updated_at,"
                    " first_payload_collected_at, history_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(corp_code, bsns_year, reprt_code, idx_code) DO UPDATE SET"
                    " idx_cl_code=excluded.idx_cl_code, idx_nm=excluded.idx_nm,"
                    " idx_val=excluded.idx_val, stock_code=excluded.stock_code,"
                    " stlm_dt=excluded.stlm_dt, updated_at=excluded.updated_at,"
                    " history_id=excluded.history_id,"
                    " first_payload_collected_at=COALESCE(indicators.first_payload_collected_at,"
                    " excluded.first_payload_collected_at)",
                    (r["corp_code"], r["bsns_year"], r["reprt_code"], r.get("idx_cl_code"),
                     r["idx_code"], r.get("idx_nm"), _to_float(r.get("idx_val")),
                     r.get("stock_code"), r.get("stlm_dt"), now, first_collected, hid),
                )
                written += 1
        con.execute("RELEASE upsert_indicators")
        return written
    except Exception:
        con.execute("ROLLBACK TO upsert_indicators")
        con.execute("RELEASE upsert_indicators")
        raise


def upsert_accounts(
    con,
    rows: list[dict],
    request_context: dict | None = None,
    filings_by_rcept: dict | None = None,
) -> int:
    """계정 current 갱신 + 실제 접수번호별 응답 보존. 같은 savepoint라 current 실패 시 이력도 rollback.

    같은 번호·같은 내용이면 last만 갱신하고 first를 유지한다. 오래된 번호는 이력에만
    쌓고 current를 후퇴시키지 않으며, 번호 미상은 알려진 current 번호를 지우지 않는다
    (이력만). 같은 PK에 sj_div가 엇갈리면(입력 내·기존 current와) 이력은 보존하고 해당
    current 키는 건너뛰며 provenance에 충돌 상세를 남긴다. 생략된 current 행은 지우지
    않는다. rcept_dt는 일치한 목록 메타에서만 취하고 번호 prefix로 만들지 않는다.
    트리거 번호를 응답에 붙이지 않는다. commit은 호출자가 유지한다. 반환은 실제 current
    쓰기 수(충돌·회귀 스킵 제외)다.
    """
    if not rows:
        return 0
    now = _now()
    req = _redact_sensitive(dict(request_context or {}))
    by_rcept = filings_by_rcept or {}
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        rcept = r.get("rcept_no")
        rcept = str(rcept).strip() or None if rcept is not None else None
        groups.setdefault(
            (r["corp_code"], r["bsns_year"], r["reprt_code"], rcept), []
        ).append(r)
    con.execute("SAVEPOINT upsert_accounts")
    try:
        pk_sj: dict[tuple, set] = {}
        for r in rows:
            pk = (r["corp_code"], r["bsns_year"], r["reprt_code"], r["fs_div"], r["account_nm"])
            pk_sj.setdefault(pk, set()).add(r.get("sj_div"))
        incoming_conflict = {pk for pk, sjs in pk_sj.items() if len(sjs) > 1}
        existing: dict[tuple, tuple] = {}
        for pk in pk_sj:
            hit = con.execute(
                "SELECT sj_div, rcept_no FROM accounts WHERE corp_code=? AND bsns_year=?"
                " AND reprt_code=? AND fs_div=? AND account_nm=?", pk,
            ).fetchone()
            if hit is not None:
                existing[pk] = (hit[0], hit[1])
        written = 0
        collided: set[tuple] = set()
        for (corp, year, reprt, rcept), grows in groups.items():
            ordered = sorted(grows, key=lambda r: (
                str(r.get("fs_div") or ""), str(r.get("sj_div") or ""),
                str(r.get("account_nm") or "")))
            pjson = _stable_json(ordered)
            phash = _payload_hash(pjson)
            matched = by_rcept.get(rcept) if rcept else None
            matched = dict(matched) if isinstance(matched, dict) else None
            rcept_dt = (matched or {}).get("rcept_dt") or None
            collisions: list[dict] = []
            for r in grows:
                pk = (r["corp_code"], r["bsns_year"], r["reprt_code"],
                      r["fs_div"], r["account_nm"])
                if pk in incoming_conflict:
                    if pk not in collided:
                        collisions.append({
                            "pk": list(pk), "kind": "incoming_sj_div_conflict",
                            "sj_divs": sorted(
                                [s if s is None else str(s) for s in pk_sj[pk]],
                                key=lambda s: "" if s is None else s),
                        })
                    collided.add(pk)
                elif pk in existing and r.get("sj_div") != existing[pk][0]:
                    if pk not in collided:
                        collisions.append({
                            "pk": list(pk), "kind": "existing_sj_div_conflict",
                            "incoming_sj_div": r.get("sj_div"),
                            "existing_sj_div": existing[pk][0],
                        })
                    collided.add(pk)
            provenance: dict = {"request": req, "matched_filing": matched,
                                "rcept_no_source": "response" if rcept else "unknown"}
            if collisions:
                provenance["collisions"] = collisions
            hit = con.execute(
                "SELECT history_id, first_payload_collected_at FROM financial_response_history"
                " WHERE source='accounts' AND corp_code=? AND bsns_year=? AND reprt_code=?"
                " AND idx_cl_code='' AND COALESCE(rcept_no,'')=COALESCE(?,'')"
                " AND payload_hash=?",
                (corp, year, reprt, rcept, phash),
            ).fetchone()
            if hit is not None:
                hid = hit[0]
                first_collected = hit[1] or now
                con.execute(
                    "UPDATE financial_response_history SET last_observed_at=? WHERE history_id=?",
                    (now, hid),
                )
            else:
                cur = con.execute(
                    "INSERT INTO financial_response_history (source, corp_code, bsns_year, reprt_code,"
                    " idx_cl_code, rcept_no, rcept_dt, payload_hash, payload_json, provenance_json,"
                    " first_payload_collected_at, last_observed_at)"
                    " VALUES ('accounts',?,?,?,?,?,?,?,?,?,?,?)",
                    (corp, year, reprt, "", rcept, rcept_dt, phash, pjson,
                     _stable_json(provenance), now, now),
                )
                hid = cur.lastrowid
                first_collected = now
            dedup: dict[tuple, dict] = {}
            for r in grows:
                pk = (r["corp_code"], r["bsns_year"], r["reprt_code"],
                      r["fs_div"], r["account_nm"])
                if pk in collided:
                    continue
                known = existing.get(pk, (None, None))[1]
                if rcept is None and known is not None:
                    continue
                if rcept is not None and known is not None and _is_older_receipt(rcept, known):
                    continue
                dedup[pk] = r
            for r in dedup.values():
                pk = (r["corp_code"], r["bsns_year"], r["reprt_code"],
                      r["fs_div"], r["account_nm"])
                con.execute(
                    "INSERT INTO accounts (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_nm,"
                    " amount, stock_code, currency, updated_at, rcept_no,"
                    " first_payload_collected_at, history_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(corp_code, bsns_year, reprt_code, fs_div, account_nm) DO UPDATE SET"
                    " sj_div=excluded.sj_div, amount=excluded.amount,"
                    " stock_code=excluded.stock_code, currency=excluded.currency,"
                    " updated_at=excluded.updated_at, rcept_no=excluded.rcept_no,"
                    " history_id=excluded.history_id,"
                    " first_payload_collected_at=COALESCE(accounts.first_payload_collected_at,"
                    " excluded.first_payload_collected_at)",
                    (r["corp_code"], r["bsns_year"], r["reprt_code"], r["fs_div"], r.get("sj_div"),
                     r["account_nm"], _to_float(r.get("thstrm_amount")), r.get("stock_code"),
                     r.get("currency"), now, rcept, first_collected, hid),
                )
                written += 1
                existing[pk] = (r.get("sj_div"), rcept)
        con.execute("RELEASE upsert_accounts")
        if collided:
            print(f"[accounts] sj_div 충돌 {len(collided)}키 current 스킵(이력 보존):"
                  f" {sorted(collided)[:5]}")
        return written
    except Exception:
        con.execute("ROLLBACK TO upsert_accounts")
        con.execute("RELEASE upsert_accounts")
        raise


def existing_corps(con, table: str, year: str, reprt: str) -> set[str]:
    """해당 table의 period(bsns_year+reprt_code)를 이미 보유한 corp_code 집합.
    table은 코드 내부 상수(indicators/accounts)만 전달 — SQL 인젝션 무관."""
    cur = con.execute(
        f"SELECT DISTINCT corp_code FROM {table} WHERE bsns_year=? AND reprt_code=?",
        (year, reprt),
    )
    return {row[0] for row in cur}


# ── 유니버스 (corpCode.xml) ─────────────────────────────────────────────────────

def _download_corpcode(key: str) -> bytes:
    resp = requests.get(CORPCODE_API_URL, params={"crtfc_key": key}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.content


def _corpcode_zip(
    key: str,
    cache_path: Path = CORPCODE_CACHE,
    ttl_sec: int = CORPCODE_TTL_SEC,
    downloader=_download_corpcode,
) -> bytes:
    """corpCode.xml zip 바이트. cache_path가 ttl 이내면 재사용, 아니면 받아서 캐시.

    corpCode.xml은 신규상장/상폐로만 바뀌어 하루 1회면 충분 — 배치마다 ~수MB zip
    재다운로드·재파싱 낭비. 캐시 손상 시 zipfile 파싱에서 터지므로 호출부에서 폴백.
    downloader는 테스트 주입용.
    """
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < ttl_sec:
        return cache_path.read_bytes()
    data = downloader(key)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(data)
    return data


def fetch_listed_corps(key: str) -> list[tuple[str, str, str]]:
    """corpCode.xml → 상장사(stock_code 있는 것)만 (corp_code, stock_code, corp_name)."""
    try:
        zip_bytes = _corpcode_zip(key)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xml_name = next(n for n in zf.namelist() if n.endswith(".xml"))
            xml = zf.read(xml_name)
    except (zipfile.BadZipFile, StopIteration):
        # 캐시 손상 → 강제 재다운로드 후 1회 재시도
        CORPCODE_CACHE.unlink(missing_ok=True)
        with zipfile.ZipFile(io.BytesIO(_corpcode_zip(key))) as zf:
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


# 공시목록(list.json)에서 정기보고서만 뽑기 위한 상수.
PERIODIC_PBLNTF_TY = "A"          # 정기공시
LISTED_CORP_CLS = ("Y", "K")      # 유가증권 / 코스닥 (E=기타·N=코넥스 제외)
# report_nm 이름 → reprt_code. 분기보고서는 1Q/3Q가 동명이라 여기 없음.
REPORT_NAME_REPRT = {"사업보고서": "11011", "반기보고서": "11012"}
# 12월 결산 가정: 3월말 결산기 = 1분기, 9월말 = 3분기.
QUARTER_MONTH_REPRT = {"03": "11013", "09": "11014"}
_REPORT_NM_RE = re.compile(r"^(?:\[[^\]]*\])?\s*(\S+?)\s*\((\d{4})\.(\d{2})\)\s*$")


def parse_report_period(report_nm: str) -> tuple[str, str, str] | None:
    """공시 report_nm → (사업연도, reprt_code, 결산월). 정기보고서가 아니면 None.

    "[기재정정]반기보고서 (2026.06)" → ("2026", "11012", "06")
    분기보고서는 이름만으로 1분기·3분기를 못 가르므로 결산월로 판정한다(12월 결산 가정).
    ponytail: 비12월 결산 법인은 이 가정이 어긋난다. 분기는 결산월이 03/09가 아니면
    None으로 떨궈 스킵하고, 사업·반기는 그대로 요청한 뒤 결산월 대조·무응답 집계로 드러낸다.
    """
    m = _REPORT_NM_RE.match(report_nm)
    if not m:
        return None
    kind, year, month = m.groups()
    if kind in REPORT_NAME_REPRT:
        return year, REPORT_NAME_REPRT[kind], month
    if kind == "분기보고서":
        reprt = QUARTER_MONTH_REPRT.get(month)
        return (year, reprt, month) if reprt else None
    return None


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _minimal_filing(f: dict) -> dict:
    """provenance용 공시 최소 메타. 키·불필요 필드 없이 식별·대조용만."""
    return {"rcept_no": f.get("rcept_no"), "rcept_dt": f.get("rcept_dt"),
            "corp_code": f.get("corp_code"), "corp_name": f.get("corp_name"),
            "report_nm": f.get("report_nm")}


# ── 래퍼 러너 ───────────────────────────────────────────────────────────────────

def _fetch_indicator_rows(chunk, year, reprt, key, session) -> list[dict]:
    """청크당 4카테고리 순회. 카테고리마다 sleep. 일부 실패는 경고 후 성공분만 반환.

    전부 실패하면 첫 오류를 그대로 raise(해당 chunk 실패, 이전 commit 유지).
    실패 카테고리의 기존 current는 건드리지 않는다(삭제 없음).
    """
    buf: list[dict] = []
    first_err: Exception | None = None
    for cat in IDX_CATEGORIES:
        try:
            buf += fetch_indicators_chunk(chunk, year, reprt, cat, key, session)
        except DartAPIError as exc:
            if first_err is None:
                first_err = exc
            print(f"[indicators {year} {reprt} {cat}] API 오류, 해당 분류 스킵: {exc}")
        time.sleep(DELAY)
    if not buf and first_err is not None:
        raise first_err
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


def _process_source(con, source, universe, year, reprt, limit, force, key, session,
                    request_context_base: dict | None = None,
                    filings_by_rcept: dict | None = None) -> dict:
    """한 source × 한 period 적재. skip→chunk 호출→upsert→chunk commit.

    API 오류(DartAPIError)는 그대로 raise — 이전 chunk commit·current는 유지된다.
    매 chunk 요청 맥락을 upsert provenance로 넘긴다(키 제외). upsert가 이력·current를
    같은 savepoint로 처리하므로 부분 실패 시 해당 chunk분만 rollback된다.
    """
    table, fetch_fn, upsert_fn = SOURCES[source]
    done = set() if force else existing_corps(con, table, year, reprt)
    targets = [cc for cc in universe if cc not in done]
    if limit is not None:
        targets = targets[:limit]
    chunks = list(_chunks(targets, BATCH))
    print(f"[{source} {year} {reprt}] 대상 {len(targets)} (skip {len(universe) - len(targets)}), chunk {len(chunks)}")

    prows = 0
    answered: dict[str, str | None] = {}  # 이번 호출이 실제로 응답한 corp → stlm_dt
    for ci, chunk in enumerate(chunks, 1):
        rows = fetch_fn(chunk, year, reprt, key, session)
        req = dict(request_context_base or {})
        req.update({"source": source, "bsns_year": year, "reprt_code": reprt,
                    "corp_codes": list(chunk), "force": bool(force)})
        prows += upsert_fn(con, rows, request_context=req, filings_by_rcept=filings_by_rcept)
        for r in rows:
            answered.setdefault(r["corp_code"], r.get("stlm_dt"))
        con.commit()  # chunk 단위 보존 → 중단 시 재실행 이어받기
        print(f"[{source} {year} {reprt}][chunk {ci}/{len(chunks)}] corps={len(chunk)} rows={len(rows)}")
    return {"source": source, "year": year, "reprt": reprt, "targets": len(targets),
            "rows": prows, "answered": answered}


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
                r = _process_source(con, source, universe, year, reprt, limit, force, key, session,
                                    request_context_base={"mode": "run"})
                stats["runs"].append({k: v for k, v in r.items() if k != "answered"})
                stats["rows"] += r["rows"]

    return stats


def run_from_filings(
    filed_on: str,
    db_path: Path = DEFAULT_DB_PATH,
    sources: list[str] | None = None,
    key: str | None = None,
) -> dict:
    """filed_on(YYYYMMDD)에 접수된 정기보고서 제출사만 적재.

    제출 시기를 달력으로 추측하지 않고, 공시목록이 알려준 (사업연도, 보고서)를 그대로 쓴다.
    대상이 이미 '그날 낸 곳'으로 좁혀져 있어 force 적재 — 정정·첨부 재공시가 기존 행을 덮는다.
    제출이 없는 날은 목록 1콜로 끝난다.
    트리거 공시 목록은 매칭된 응답 그룹의 provenance에만 싣고, 트리거 번호를 응답 번호로
    둔갑시키지 않는다. 목록 자체의 장기 보관은 phase0 manifest가 맡는다.
    """
    key = key or get_api_key()
    sources = sources or list(SOURCES)
    filings, _ = fetch_all_filings(key, filed_on, filed_on, pblntf_ty=PERIODIC_PBLNTF_TY)

    groups: dict[tuple[str, str], list[str]] = {}
    months: dict[tuple[str, str], set[str]] = {}
    filings_by_period: dict[tuple[str, str], list[dict]] = {}
    unparsed: list[str] = []
    for f in filings:
        if f.get("corp_cls") not in LISTED_CORP_CLS:
            continue
        parsed = parse_report_period(str(f.get("report_nm", "")))
        if parsed is None:
            unparsed.append(str(f.get("report_nm", "")))
            continue
        year, reprt, month = parsed
        codes = groups.setdefault((year, reprt), [])
        if f["corp_code"] not in codes:
            codes.append(f["corp_code"])
        months.setdefault((year, reprt), set()).add(month)
        filings_by_period.setdefault((year, reprt), []).append(f)

    targets = sum(len(v) for v in groups.values())
    print(f"[{filed_on}] 정기공시 {len(filings)} → 상장 제출사 {targets}, period {len(groups)}")
    if unparsed:
        print(f"  period 판정 불가 {len(unparsed)}건 스킵: {sorted(set(unparsed))[:5]}")

    stats = {"filed_on": filed_on, "filings": len(filings), "targets": targets,
             "runs": [], "rows": 0, "missing": {}, "stlm_mismatch": {}}
    if not groups:
        return stats

    filings_by_rcept = {str(f.get("rcept_no")): _minimal_filing(f)
                        for f in filings if f.get("rcept_no")}
    session = requests.Session()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_rw(db_path) as con:
        ensure_schema(con)
        upsert_corps(con, fetch_listed_corps(key))  # 신규 상장사 이름 보강(zip 캐시라 사실상 0콜)
        con.commit()

        for (year, reprt), corp_codes in sorted(groups.items()):
            answered = None
            base = {"mode": "run_from_filings", "filed_on": filed_on,
                    "trigger_filings": [_minimal_filing(f)
                                        for f in filings_by_period.get((year, reprt), [])]}
            for source in sources:
                r = _process_source(con, source, corp_codes, year, reprt, None, True, key, session,
                                    request_context_base=base,
                                    filings_by_rcept=filings_by_rcept)
                if source == "indicators":
                    answered = r["answered"]
                stats["runs"].append({k: v for k, v in r.items() if k != "answered"})
                stats["rows"] += r["rows"]
            # 지표를 안 받은 실행(--source accounts)은 대조할 응답이 없다 → 검증 생략.
            if answered is not None:
                _report_period_gaps(stats, year, reprt, corp_codes, answered, months[(year, reprt)])

    return stats


def _report_period_gaps(stats, year, reprt, corp_codes, answered, expected_months) -> None:
    """적재 후 검증: 이번 호출에 응답이 없던 회사 + 결산월이 공시와 어긋난 회사를 집계·출력.

    12월 결산 가정이 틀린 회사가 여기서 드러난다. 잘못된 period로 요청하면 DART가
    무자료를 주거나(무응답), 그 회사의 실제 결산기 데이터를 주기(결산월 불일치) 때문.
    answered는 이번 지표 호출이 실제로 돌려준 corp→stlm_dt — DB를 조회하면 정정 공시에
    응답이 비어도 예전 행 때문에 무응답이 가려진다.
    """
    wanted = set(corp_codes)
    missing = sorted(wanted - set(answered))
    mismatch = sorted(cc for cc, dt in answered.items() if dt and dt[5:7] not in expected_months)
    label = f"{year}-{reprt}"
    if missing:
        stats["missing"][label] = missing
        print(f"  [{label}] 무응답 {len(missing)}/{len(wanted)}사: {missing[:5]}")
    if mismatch:
        stats["stlm_mismatch"][label] = mismatch
        print(f"  [{label}] 결산월 불일치 {len(mismatch)}사(공시 {sorted(expected_months)}): {mismatch[:5]}")


# ── 공시 이력 phase 0 (§10): 목록 선행 확보 ──────────────────────────────────────
# ETF 경로·기존 스키마·current writer 불변. 이 절은 파일 산출물(manifest.json)만 만든다.

LIST_API_URL = f"{BASE_URL}/list.json"
HISTORY_PBLNTF_TY = "A"          # 정기공시만
HISTORY_LAST_REPRT_AT = "N"      # 정정 포함
HISTORY_PAGE_COUNT = 100         # 페이지당 최대
HISTORY_WINDOW_DAYS = 90         # 회사 미지정 조회 3개월 제한 준수
HISTORY_MAX_PAGES = 500          # 무한 루프 방지 상한
HISTORY_ARTIFACT = "financial_filing_history_manifest"
KNOWN_REPRT_CODES = ("11011", "11012", "11013", "11014")
DEFAULT_HISTORY_DIR = Path(__file__).resolve().parents[1] / "exports" / "financial_filing_history"
EARLIEST_CAVEAT = ("queried_range_only: earliest within the fetched range,"
                   " not a verified lifetime earliest")

_DATE_RE = re.compile(r"^\d{8}$")
_CORP_RE = re.compile(r"^\d{8}$")
_RCEPT_RE = re.compile(r"^\d{1,32}$")   # 숫자만 → 파일명 삽입 안전
_KEY_RE = re.compile(r"crtfc_key\s*=\s*[^&\s'\";]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
MAX_ERROR_TEXT = 500


def _parse_yyyymmdd(s: str) -> date:
    if not s or not _DATE_RE.match(s):
        raise ValueError(f"날짜 형식 오류(YYYYMMDD): {s!r}")
    try:
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        raise ValueError(f"존재하지 않는 날짜: {s!r}") from None


def _validate_corp_code(s: str | None) -> str | None:
    if s is None:
        return None
    if not _CORP_RE.match(s):
        raise ValueError(f"corp_code 형식 오류(8자리 숫자): {s!r}")
    return s


def _validate_rcept_no(s: str) -> str:
    if not s or not _RCEPT_RE.match(s):
        raise ValueError(f"rcept_no 형식 오류(숫자만): {s!r}")
    return s


def _validate_reprt_code(s: str) -> str:
    if s not in KNOWN_REPRT_CODES:
        raise ValueError(f"reprt_code는 {list(KNOWN_REPRT_CODES)} 중 하나: {s!r}")
    return s


def _sanitize_error(text: object, key: str | None = None) -> str:
    """예외·오류 텍스트에서 인증키·URL 제거. 산출물·출력에 키가 못 들어가게."""
    s = str(text)
    if key:
        s = s.replace(key, "<redacted>")
    s = _KEY_RE.sub("crtfc_key=<redacted>", s)
    s = _URL_RE.sub("<url-redacted>", s)
    s = "".join(ch for ch in s if ch in ("\n", "\t") or ord(ch) >= 32)
    return s[:MAX_ERROR_TEXT]


def _as_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _split_windows(begin: date, end: date, max_days: int = HISTORY_WINDOW_DAYS) -> list[tuple[date, date]]:
    """[begin, end]를 max_days 이하 구간으로 분할. begin > end면 ValueError."""
    if begin > end:
        raise ValueError(f"시작일이 종료일보다 늦음: {begin:%Y%m%d} > {end:%Y%m%d}")
    out: list[tuple[date, date]] = []
    cur = begin
    while cur <= end:
        nxt = min(cur + timedelta(days=max_days - 1), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def _history_list_params(begin_s: str, end_s: str, page_no: int, corp_code: str | None) -> dict:
    """list.json 명시 파라미터. last_reprt_at=N 정정 포함, pblntf_ty=A 정기공시."""
    params: dict = {
        "bgn_de": begin_s,
        "end_de": end_s,
        "pblntf_ty": HISTORY_PBLNTF_TY,
        "last_reprt_at": HISTORY_LAST_REPRT_AT,
        "page_no": page_no,
        "page_count": HISTORY_PAGE_COUNT,
    }
    if corp_code is not None:
        params["corp_code"] = corp_code
    return params


def fetch_history_window(
    key: str,
    begin_s: str,
    end_s: str,
    corp_code: str | None = None,
    session: requests.Session | None = None,
    timeout: int = REQUEST_TIMEOUT,
) -> tuple[list[dict], dict]:
    """한 구간 list.json 전 페이지 수집. 실패해도 raise 없이 info에 미완료 기록.

    첫 000 페이지 광고값(total_count/total_page) 고정 후 전 페이지·UNIQUE
    접수번호 대조. 중복·중간 013·광고 변경·누락 행은 incomplete으로 남기고
    받은 행은 그대로 돌려준다. 첫 페이지 013만 빈 구간 완료다.
    """
    http = session or requests
    filings: list[dict] = []
    info: dict = {"begin": begin_s, "end": end_s, "pages_fetched": 0,
                  "total_count": 0, "total_page": 0, "fetched": 0,
                  "complete": False, "calls": 0, "error": None}
    page_no = 1
    advertised_count: int | None = None
    advertised_pages: int | None = None
    while True:
        if page_no > HISTORY_MAX_PAGES:
            info["error"] = f"page_cap_reached: {HISTORY_MAX_PAGES}"
            break
        params = _history_list_params(begin_s, end_s, page_no, corp_code)
        try:
            resp = http.get(LIST_API_URL, params={"crtfc_key": key, **params}, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            info["calls"] += 1
            info["error"] = f"http_error: {_sanitize_error(exc, key)}"
            break
        info["calls"] += 1
        info["pages_fetched"] += 1
        if not isinstance(payload, dict):
            info["error"] = "http_error: non-dict json payload"
            break
        status = payload.get("status")
        if status == "013":
            if page_no == 1:
                info["complete"] = True
            else:
                info["error"] = "incomplete: mid_window_no_data status=013"
            break
        if status != "000":
            info["error"] = (f"api_error status={status}: "
                             f"{_sanitize_error(payload.get('message'), key)}")
            break
        rows = payload.get("list")
        if not isinstance(rows, list):
            info["error"] = f"incomplete: malformed_list page={page_no}"
            break
        total_count = _as_int(payload.get("total_count"), -1)
        total_page = _as_int(payload.get("total_page"), -1)
        if total_count < 0 or total_page <= 0:
            if not (total_count == 0 and total_page == 0):
                info["error"] = (f"incomplete: malformed_totals page={page_no}"
                                 f" total_count={payload.get('total_count')!r}"
                                 f" total_page={payload.get('total_page')!r}")
                filings.extend([r for r in rows if isinstance(r, dict)])
                break
        if advertised_count is None:
            advertised_count, advertised_pages = total_count, total_page
            info["total_count"], info["total_page"] = total_count, total_page
        elif total_count != advertised_count or total_page != advertised_pages:
            info["error"] = (f"incomplete: unstable_totals page={page_no}"
                             f" advertised=({advertised_count},{advertised_pages})"
                             f" got=({total_count},{total_page})")
            filings.extend([r for r in rows if isinstance(r, dict)])
            break
        bad_row = any(not isinstance(r, dict)
                      or not str(r.get("rcept_no") or "").strip()
                      or not str(r.get("rcept_dt") or "").strip() for r in rows)
        filings.extend([r for r in rows if isinstance(r, dict)])
        if bad_row:
            info["error"] = (f"incomplete: malformed_row page={page_no}"
                             " missing rcept_no/rcept_dt")
            break
        if page_no >= max(1, advertised_pages or 0):
            break
        page_no += 1
    info["fetched"] = len(filings)
    if info["error"] is None and advertised_count is not None:
        uniq = {str(r.get("rcept_no") or "") for r in filings
                if str(r.get("rcept_no") or "")}
        if advertised_count == 0:
            if len(filings) == 0:
                info["complete"] = True
            else:
                info["error"] = f"count_mismatch: fetched={len(filings)} total_count=0"
        elif info["pages_fetched"] != max(1, advertised_pages or 0):
            info["error"] = (f"incomplete: pages_mismatch"
                             f" fetched_pages={info['pages_fetched']}"
                             f" advertised_pages={advertised_pages}")
        elif len(uniq) != advertised_count or len(filings) != advertised_count:
            info["error"] = (f"count_mismatch: fetched={len(filings)}"
                             f" unique={len(uniq)} total_count={advertised_count}")
        else:
            info["complete"] = True
    return filings, info


def _history_group_key(report_nm: str) -> tuple[str, str | None, str | None]:
    """원문 보고서명 → (종류, 연도, 기말월). 판정 불가면 ("unparsed", None, None)."""
    m = _REPORT_NM_RE.match(report_nm or "")
    if not m:
        return ("unparsed", None, None)
    return (m.group(1), m.group(2), m.group(3))


def _group_filings(filings: list[dict]) -> list[dict]:
    """회사·보고서 종류·기말 연월 그룹 + 그룹별 최초 접수일 후보.

    reprt_code 미확정(비12월 분기 등)은 NULL+사유로 남기고 버리지 않는다.
    earliest는 조회 구간 내 최초일 뿐 생애 최초 검증이 아니다(EARLIEST_CAVEAT).
    """
    groups: dict[tuple, dict] = {}
    for f in filings:
        report_nm = str(f.get("report_nm") or "")
        kind, year, month = _history_group_key(report_nm)
        parsed = parse_report_period(report_nm)
        if parsed is None:
            if kind == "분기보고서":
                reason: str | None = f"non_december_quarter_month:{month}"
            elif kind == "unparsed":
                reason = "unparsed_report_nm"
            else:
                reason = f"unknown_report_kind:{kind}"
            pyear, reprt, pmonth = year, None, month
        else:
            reason = None
            pyear, reprt, pmonth = parsed
        gkey = (str(f.get("corp_code") or ""), kind, pyear, pmonth)
        g = groups.get(gkey)
        if g is None:
            g = {"corp_code": str(f.get("corp_code") or ""), "corp_name": f.get("corp_name"),
                 "report_kind": kind, "period_year": pyear, "end_month": pmonth,
                 "reprt_code": reprt, "reprt_unresolved_reason": reason,
                 "count": 0, "earliest_candidate": None, "earliest_caveat": EARLIEST_CAVEAT}
            groups[gkey] = g
        g["count"] += 1
        cand = (str(f.get("rcept_dt") or ""), str(f.get("rcept_no") or ""))
        cur = g["earliest_candidate"]
        if cur is None or cand < (cur["rcept_dt"], cur["rcept_no"]):
            g["earliest_candidate"] = {"rcept_no": str(f.get("rcept_no") or ""),
                                       "rcept_dt": str(f.get("rcept_dt") or ""),
                                       "report_nm": report_nm}
    return sorted(groups.values(),
                  key=lambda g: (g["corp_code"], g["report_kind"],
                                 g["period_year"] or "", g["end_month"] or ""))


def collect_filings_history(
    key: str,
    begin_s: str,
    end_s: str,
    corp_code: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """구간 분할 → 전 윈도우 수집 → 접수번호 중복 제거 → manifest dict. 파일 I/O 없음."""
    corp_code = _validate_corp_code(corp_code)
    begin = _parse_yyyymmdd(begin_s)
    end = _parse_yyyymmdd(end_s)
    collected_at = _now()
    all_rows: list[dict] = []
    win_infos: list[dict] = []
    for wb, we in _split_windows(begin, end):
        rows, info = fetch_history_window(key, wb.strftime("%Y%m%d"), we.strftime("%Y%m%d"),
                                          corp_code, session)
        all_rows.extend(rows)
        win_infos.append(info)
    seen: dict[str, dict] = {}
    dupes = 0
    for r in all_rows:
        rno = str(r.get("rcept_no") or "")
        if not rno:
            continue
        if rno in seen:
            dupes += 1
            continue
        row = dict(r)
        row["first_observed_at"] = collected_at
        seen[rno] = row
    filings = sorted(seen.values(),
                     key=lambda r: (str(r.get("rcept_dt") or ""), str(r.get("rcept_no") or "")))
    return {
        "artifact": HISTORY_ARTIFACT,
        "query": {"begin": begin_s, "end": end_s, "corp_code": corp_code,
                  "pblntf_ty": HISTORY_PBLNTF_TY, "last_reprt_at": HISTORY_LAST_REPRT_AT,
                  "collected_at": collected_at},
        "complete": all(w["complete"] for w in win_infos),
        "total_calls": sum(w["calls"] for w in win_infos),
        "duplicates_skipped": dupes,
        "carried_observations": 0,
        "windows": win_infos,
        "filings": filings,
        "groups": _group_filings(filings),
        "notes": [
            "earliest_candidate is earliest within the queried range only,"
            " not a verified lifetime earliest.",
            "complete=false means a window failed or count-mismatched;"
            " already fetched rows are preserved, never labeled complete.",
        ],
    }


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _query_snapshot_name(begin_s: str, end_s: str, corp_code: str | None) -> str:
    return f"{begin_s}_{end_s}_{corp_code or 'all'}.json"


def _history_prev_corp(prev: dict) -> str | None:
    if "corp_code" in prev:
        return prev.get("corp_code")
    return (prev.get("query") or {}).get("corp_code")


def _ensure_history_dir_compatible(history_dir: Path, corp_code: str | None) -> None:
    """깨진 manifest·다른 corp 재사용은 덮어쓰기 전 ValueError로 거부."""
    mpath = history_dir / "manifest.json"
    if mpath.exists():
        try:
            prev = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError(
                f"corrupted manifest at {mpath}: refuse to overwrite evidence") from None
        if not isinstance(prev, dict) or prev.get("artifact") != HISTORY_ARTIFACT:
            raise ValueError(
                f"corrupted manifest at {mpath}: refuse to overwrite evidence")
        if _history_prev_corp(prev) != corp_code:
            raise ValueError(
                f"incompatible corp context: existing {_history_prev_corp(prev)!r}"
                f" vs requested {corp_code!r}; use different history_dir")
    qdir = history_dir / "queries"
    if qdir.exists():
        for p in qdir.glob("*.json"):
            snap = _read_json(p)
            if not isinstance(snap, dict) or snap.get("artifact") != HISTORY_ARTIFACT:
                continue
            if (snap.get("query") or {}).get("corp_code") != corp_code:
                raise ValueError(
                    f"incompatible corp context: snapshot {p.name}"
                    f" vs requested {corp_code!r}; use different history_dir")


def run_filings_history(
    begin_s: str,
    end_s: str,
    corp_code: str | None = None,
    history_dir: Path = DEFAULT_HISTORY_DIR,
    key: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """목록 수집 → 쿼리 스냅샷 저장 → aggregate manifest.json 원자 저장. DB 쓰기 없음."""
    key = key or get_api_key()
    corp_code = _validate_corp_code(corp_code)
    _split_windows(_parse_yyyymmdd(begin_s), _parse_yyyymmdd(end_s))
    history_dir = Path(history_dir)
    _ensure_history_dir_compatible(history_dir, corp_code)
    current = collect_filings_history(key, begin_s, end_s, corp_code, session)
    snap_path = history_dir / "queries" / _query_snapshot_name(begin_s, end_s, corp_code)
    _atomic_write_json(snap_path, current)
    prev = _read_json(history_dir / "manifest.json")
    snaps: list[dict] = []
    for p in sorted((history_dir / "queries").glob("*.json")):
        d = _read_json(p)
        if isinstance(d, dict) and d.get("artifact") == HISTORY_ARTIFACT:
            snaps.append(d)
    snaps.sort(key=lambda d: str((d.get("query") or {}).get("collected_at") or ""))
    merged: dict[str, dict] = {}
    if isinstance(prev, dict):
        for r in (prev.get("filings") or []):
            if isinstance(r, dict) and r.get("rcept_no"):
                merged[str(r["rcept_no"])] = dict(r)
    prev_times = {k: str(v.get("first_observed_at") or "")
                  for k, v in merged.items()}
    for snap in snaps:
        for r in (snap.get("filings") or []):
            if not isinstance(r, dict) or not r.get("rcept_no"):
                continue
            rno = str(r["rcept_no"])
            if rno in merged:
                earliest = min(str(merged[rno].get("first_observed_at") or ""),
                               str(r.get("first_observed_at") or ""))
                row = dict(r)
                row["first_observed_at"] = earliest
                merged[rno] = row
            else:
                merged[rno] = dict(r)
    filings = sorted(merged.values(),
                     key=lambda r: (str(r.get("rcept_dt") or ""),
                                    str(r.get("rcept_no") or "")))
    curr_times = {str(r.get("rcept_no")): str(r.get("first_observed_at") or "")
                  for r in (current.get("filings") or [])
                  if isinstance(r, dict) and r.get("rcept_no")}
    carried = sum(1 for rno, ct in curr_times.items()
                  if rno in prev_times and prev_times[rno] and prev_times[rno] < ct)
    agg_windows: list[dict] = []
    for snap in snaps:
        agg_windows.extend(snap.get("windows") or [])
    aggregate = {
        "artifact": HISTORY_ARTIFACT,
        "query": current["query"],
        "corp_code": corp_code,
        "queries": sorted(p.name for p in (history_dir / "queries").glob("*.json")),
        "complete": bool(snaps) and all(s.get("complete") for s in snaps),
        "current_query_complete": bool(current.get("complete")),
        "total_calls": sum(int(s.get("total_calls") or 0) for s in snaps),
        "duplicates_skipped": sum(int(s.get("duplicates_skipped") or 0) for s in snaps),
        "carried_observations": carried,
        "windows": agg_windows,
        "filings": filings,
        "groups": _group_filings(filings),
        "notes": [
            "earliest_candidate is earliest within the queried range only,"
            " not a verified lifetime earliest.",
            "complete=false means a window failed or count-mismatched;"
            " already fetched rows are preserved, never labeled complete.",
            "manifest is aggregate of queries/* snapshots; current_query_complete"
            " is this query only.",
        ],
    }
    _atomic_write_json(history_dir / "manifest.json", aggregate)
    return aggregate


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


# ── 공시 이력 phase 0 (§10): XBRL 실증 ───────────────────────────────────────────
# 산출물: xbrl/<rcept_no>_<reprt_code>.zip, probe.json. DB 쓰기·회계 매핑 없음.
# ZIP 확보는 금액 복구 완료가 아니다(보고서에 no_claim_of_recovery 명시).

XBRL_API_URL = f"{BASE_URL}/fnlttXbrl.xml"
XBRL_AUTH_STATUSES = ("010", "011", "012", "901")
XBRL_INSTANCE_TAG = "{http://www.xbrl.org/2003/instance}xbrl"
ERROR_XML_MAX_BYTES = 1_048_576
MAX_XBRL_INSTANCE_BYTES = 8 * 1024 * 1024
MAX_XBRL_CONTEXTS = 2000
MAX_XBRL_FACTS = 5000
MAX_XBRL_INSTANCES = 10


def _xbrl_error_kind(status: str | None) -> str:
    if status in XBRL_AUTH_STATUSES:
        return "auth_error"
    return {"014": "file_not_found", "020": "rate_limit",
            "013": "no_data"}.get(status or "", "api_error")


def _parse_error_xml(data: bytes) -> tuple[str | None, str | None]:
    """DART 오류 XML이면 (status, message), 아니면 (None, None)."""
    if len(data) > ERROR_XML_MAX_BYTES or b"<status" not in data:
        return None, None
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None, None
    status = root.findtext("status")
    if status is None:
        return None, None
    return status.strip(), (root.findtext("message") or "").strip()


def _localname(tag) -> str:
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag.rsplit(":", 1)[-1]


def _parse_xbrl_context(el) -> dict:
    entity_id, scheme = None, None
    for sub in el.iter():
        if _localname(sub.tag) == "identifier":
            entity_id = (sub.text or "").strip()
            scheme = sub.get("scheme")
            break
    period: dict = {}
    for sub in el:
        if _localname(sub.tag) != "period":
            continue
        for p in sub:
            pln = _localname(p.tag)
            if pln in ("instant", "startDate", "endDate", "forever"):
                period[pln] = (p.text or "").strip()
    dims = [{"dimension": sub.get("dimension"), "member": (sub.text or "").strip()}
            for sub in el.iter() if _localname(sub.tag) == "explicitMember"]
    return {"id": el.get("id"), "entity": {"scheme": scheme, "identifier": entity_id},
            "period": period, "explicit_dimensions": dims}


def _parse_xbrl_unit(el) -> dict:
    out: dict = {"id": el.get("id"), "measures": [], "divide": None}
    for sub in el:
        ln = _localname(sub.tag)
        if ln == "measure":
            out["measures"].append((sub.text or "").strip())
        elif ln == "divide":
            num, den = [], []
            for part in sub:
                pln = _localname(part.tag)
                vals = [(m.text or "").strip() for m in part.iter()
                        if _localname(m.tag) == "measure"]
                if pln == "unitNumerator":
                    num = vals
                elif pln == "unitDenominator":
                    den = vals
            out["divide"] = {"numerator": num, "denominator": den}
    return out


def _parse_xbrl_instance(xml_bytes: bytes) -> dict:
    """인스턴스 XML → context/unit/fact 메타. 값은 문자열 그대로(회계 매핑 없음)."""
    namespaces: dict[str, str] = {}
    for _ev, ns in ET.iterparse(io.BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = ns
        namespaces[prefix or ""] = uri
    root = ET.fromstring(xml_bytes)
    contexts, ctx_truncated, total_ctx = [], False, 0
    for el in root.iter():
        if _localname(el.tag) != "context":
            continue
        total_ctx += 1
        if len(contexts) >= MAX_XBRL_CONTEXTS:
            ctx_truncated = True
            continue
        contexts.append(_parse_xbrl_context(el))
    units = [_parse_xbrl_unit(el) for el in root.iter() if _localname(el.tag) == "unit"]
    facts, fact_truncated, total_facts = [], False, 0
    for el in root.iter():
        ctx = el.get("contextRef")
        if ctx is None:
            continue
        total_facts += 1
        if len(facts) >= MAX_XBRL_FACTS:
            fact_truncated = True
            continue
        facts.append({"name": _localname(el.tag), "qname": el.tag,
                      "contextRef": ctx, "unitRef": el.get("unitRef"),
                      "decimals": el.get("decimals"),
                      "value": (el.text or "").strip()})
    return {"contexts": contexts, "total_contexts": total_ctx,
            "contexts_truncated": ctx_truncated, "units": units,
            "facts": facts, "total_facts": total_facts,
            "facts_truncated": fact_truncated, "namespaces": namespaces}


def _inspect_xbrl_zip(data: bytes, key: str | None = None) -> dict:
    """ZIP 멤버 목록 + 인스턴스 XML 메타. 압축 해제 없이 메모리 검사만.

    .xbrl 우선, .xml은 루트가 XBRL 인스턴스일 때만 인정. 모든 인스턴스를
    instances에 싣고 instance는 첫 대표다. 링크베이스만 있으면 no_instance
    (무효 ZIP이 아님). bound 초과는 exceeds_bound로 명시한다.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("invalid_zip: not a zip archive") from None
    with zf:
        members = [{"name": i.filename, "size": i.file_size,
                    "compress_size": i.compress_size} for i in zf.infolist()]
        cands = [i for i in zf.infolist()
                 if i.filename.lower().endswith((".xbrl", ".xml")) and not i.is_dir()]
        if not cands:
            return {"members": members, "instance": None, "instances": [],
                    "instance_skipped": "no_xml_member",
                    "instances_truncated": False}
        cands.sort(key=lambda i: (0 if i.filename.lower().endswith(".xbrl") else 1,
                                  i.filename))
        instances: list[dict] = []
        truncated = False
        oversized: int | None = None
        saw_parsable = False
        for cand in cands:
            if len(instances) >= MAX_XBRL_INSTANCES:
                truncated = True
                break
            if cand.file_size > MAX_XBRL_INSTANCE_BYTES:
                oversized = cand.file_size
                continue
            try:
                content = zf.read(cand.filename)
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise ValueError(
                    f"invalid_zip: unreadable member: {_sanitize_error(exc, key)}") from None
            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                continue
            saw_parsable = True
            if root.tag != XBRL_INSTANCE_TAG:
                continue
            try:
                parsed = _parse_xbrl_instance(content)
            except ET.ParseError as exc:
                raise ValueError(
                    f"invalid_zip: instance xml unparsable: "
                    f"{_sanitize_error(exc, key)}") from None
            parsed["member"] = cand.filename
            parsed["byte_size"] = len(content)
            instances.append(parsed)
        if instances:
            return {"members": members, "instance": instances[0],
                    "instances": instances, "instance_skipped": None,
                    "instances_truncated": truncated}
        if oversized is not None:
            return {"members": members, "instance": None, "instances": [],
                    "instance_skipped": f"exceeds_bound:{oversized}",
                    "instances_truncated": truncated}
        if not saw_parsable:
            raise ValueError("invalid_zip: no parsable instance xml") from None
        return {"members": members, "instance": None, "instances": [],
                "instance_skipped": "no_instance",
                "instances_truncated": truncated}


def _xbrl_zip_path(history_dir: Path, rcept_no: str, reprt_code: str) -> Path:
    return history_dir / "xbrl" / f"{rcept_no}_{reprt_code}.zip"


def _load_probe_entries(history_dir: Path) -> dict:
    prev = _read_json(history_dir / "probe.json")
    return prev if isinstance(prev, dict) else {}


def _save_probe_entry(history_dir: Path, probe_id: str, report: dict) -> dict:
    """probe.json 누적 저장. first_payload_collected_at는 최초 성공 다운로드 시각으로 고정.

    probed_at은 매번 최신 시각. first는 보존 우선: 기존 first가 있으면 유지,
    없으면 레거시 downloaded+probed_at만 증거로 승격, 현재 downloaded일 때만
    probed_at으로 신규 확정. reused/error는 발명하지 않고 NULL 유지.
    """
    entries = _load_probe_entries(history_dir)
    prev = entries.get(probe_id)
    prev_first = None
    prev_status = None
    prev_probed_at = None
    if isinstance(prev, dict):
        v = prev.get("first_payload_collected_at")
        prev_first = v if v else None
        prev_status = prev.get("status")
        p = prev.get("probed_at")
        prev_probed_at = p if p else None
    if prev_first is not None:
        report["first_payload_collected_at"] = prev_first
    elif prev_status == "downloaded" and prev_probed_at is not None:
        report["first_payload_collected_at"] = prev_probed_at
    elif report.get("status") == "downloaded":
        report["first_payload_collected_at"] = report.get("probed_at")
    else:
        report["first_payload_collected_at"] = None
    entries[probe_id] = report
    _atomic_write_json(history_dir / "probe.json", entries)
    return report


def probe_xbrl(
    rcept_no: str,
    reprt_code: str,
    history_dir: Path = DEFAULT_HISTORY_DIR,
    key: str | None = None,
    session: requests.Session | None = None,
) -> dict:
    """접수번호·보고서코드 1건 XBRL 실증. 성공 ZIP만 원자 저장, probe.json 누적.

    기존 유효 ZIP은 재다운로드 없이 재사용·검사, 손상 캐시는 버리고 재시도.
    오류(XML 014/020/인증·HTTP·무효 ZIP)는 보고서로 돌려주고 raise 하지 않는다.
    입력 검증 실패만 ValueError.
    """
    rcept_no = _validate_rcept_no(rcept_no)
    reprt_code = _validate_reprt_code(reprt_code)
    history_dir = Path(history_dir)
    key = key or get_api_key()
    http = session or requests
    probe_id = f"{rcept_no}_{reprt_code}"
    zip_path = _xbrl_zip_path(history_dir, rcept_no, reprt_code)
    report: dict = {"rcept_no": rcept_no, "reprt_code": reprt_code, "probed_at": _now(),
                    "first_payload_collected_at": None, "status": "downloaded",
                    "byte_size": None, "zip_members": None,
                    "instance": None, "instances": None, "instance_skipped": None,
                    "error_kind": None, "error": None, "recovery": "no_claim_of_recovery"}
    data: bytes | None = None
    inspected: dict | None = None
    if zip_path.exists():
        try:
            data = zip_path.read_bytes()
            inspected = _inspect_xbrl_zip(data, key)
            report["status"] = "reused"
        except (OSError, ValueError):
            zip_path.unlink(missing_ok=True)  # 손상 캐시는 버리고 재다운로드
            data, inspected = None, None
    if data is None:
        try:
            resp = http.get(XBRL_API_URL,
                            params={"crtfc_key": key, "rcept_no": rcept_no,
                                    "reprt_code": reprt_code},
                            timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.content
        except Exception as exc:
            report["status"] = "error"
            report["error_kind"] = "http_error"
            report["error"] = _sanitize_error(exc, key)
            return _save_probe_entry(history_dir, probe_id, report)
        report["byte_size"] = len(data)
        if not data.startswith(b"PK"):
            estatus, emsg = _parse_error_xml(data)
            report["status"] = "error"
            if estatus is None:
                report["error_kind"] = "invalid_zip"
                report["error"] = "not a zip archive and not a DART error xml"
            else:
                report["error_kind"] = _xbrl_error_kind(estatus)
                report["error"] = f"status={estatus}: {_sanitize_error(emsg, key)}"
            return _save_probe_entry(history_dir, probe_id, report)
        try:
            inspected = _inspect_xbrl_zip(data, key)
        except ValueError as exc:
            report["status"] = "error"
            report["error_kind"] = "invalid_zip"
            report["error"] = _sanitize_error(exc, key)
            return _save_probe_entry(history_dir, probe_id, report)
        _atomic_write_bytes(zip_path, data)
    report["byte_size"] = len(data)
    assert inspected is not None
    report["zip_members"] = inspected["members"]
    report["instance"] = inspected["instance"]
    report["instances"] = inspected.get("instances", [])
    report["instance_skipped"] = inspected["instance_skipped"]
    return _save_probe_entry(history_dir, probe_id, report)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="전 상장사 DART 재무지표 적재")
    # --self-check moved to exclusive group below
    p.add_argument("--limit", type=int, default=None, help="남은 대상 앞 N종목만")
    p.add_argument("--year", help="사업연도 강제 YYYY (기본: 최신 가용 연간)")
    p.add_argument("--reprt", default="11011",
                   help="보고서: 11011 연간 / 11013 1Q / 11012 반기 / 11014 3Q (--year 필요)")
    p.add_argument("--source", choices=[*SOURCES, "both"], default="both",
                   help="indicators(지표) / accounts(금액) / both(기본)")
    p.add_argument("--force", action="store_true", help="skip 무시 전량 재적재")
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    # --filings-on moved to exclusive group below
    # (filings-on help moved below)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--self-check", action="store_true", help="core 함수 실호출 검증만")
    g.add_argument("--filings-on", nargs="?", const="", metavar="YYYYMMDD",
                   help="그날 접수된 정기보고서 제출사만 적재 (값 생략 시 어제). --year/--reprt 무시")
    g.add_argument("--filings-history", nargs=2, metavar=("BEGIN", "END"),
                   help="공시 이력 목록 수집 (YYYYMMDD YYYYMMDD) → manifest.json")
    g.add_argument("--probe-xbrl", nargs=2, metavar=("RCEPT_NO", "REPRT_CODE"),
                   help="XBRL 원문 1건 실증 → xbrl/*.zip + probe.json")
    p.add_argument("--corp-code", default=None, help="목록 수집 회사코드(8자리, 생략 시 전체)")
    p.add_argument("--history-dir", type=Path, default=DEFAULT_HISTORY_DIR,
                   help="이력 산출물 경로")
    return p


def main() -> None:
    p = build_parser()
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return

    if args.filings_history is not None:
        try:
            manifest = run_filings_history(args.filings_history[0], args.filings_history[1],
                                           corp_code=args.corp_code, history_dir=args.history_dir)
        except ValueError as exc:
            p.error(str(exc))
        if not manifest.get("complete"):
            print("INCOMPLETE", {"mode": "filings_history", "complete": manifest["complete"],
                                 "filings": len(manifest["filings"]),
                                 "windows": len(manifest["windows"]),
                                 "calls": manifest["total_calls"]})
            raise SystemExit(1)
        print("DONE", {"mode": "filings_history", "complete": manifest["complete"],
                       "filings": len(manifest["filings"]), "windows": len(manifest["windows"]),
                       "calls": manifest["total_calls"]})
        return

    if args.probe_xbrl is not None:
        try:
            report = probe_xbrl(args.probe_xbrl[0], args.probe_xbrl[1],
                                history_dir=args.history_dir)
        except ValueError as exc:
            p.error(str(exc))
        if report.get("status") == "error":
            print("FAILED", {"mode": "probe_xbrl", "status": report["status"],
                             "byte_size": report["byte_size"],
                             "error_kind": report["error_kind"]})
            raise SystemExit(1)
        print("DONE", {"mode": "probe_xbrl", "status": report["status"],
                       "byte_size": report["byte_size"], "error_kind": report["error_kind"]})
        return

    sources_arg = list(SOURCES) if args.source == "both" else [args.source]
    if args.filings_on is not None:
        filed_on = args.filings_on or (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        print("DONE", run_from_filings(filed_on, db_path=args.db, sources=sources_arg))
        return

    periods = [(args.year, args.reprt)] if args.year else None
    stats = run(db_path=args.db, periods=periods, sources=sources_arg, limit=args.limit, force=args.force)
    print("DONE", stats)


if __name__ == "__main__":
    main()
