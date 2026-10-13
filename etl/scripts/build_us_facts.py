"""SEC XBRL 재무 원본(companyfacts) 전체 보관 + us_facts.sqlite3 적재 배치.

상세 설계는 docs/done/PLAN_US_FACTS.md. 정기보고서(10-K·10-Q 등) 전 계정을 "값이 바뀐 공시만" 버전으로 쌓는다.

Usage (from etl/):
    uv run python scripts/build_us_facts.py                                                    # 일일 갱신
    uv run python scripts/build_us_facts.py --init-from-zip D:/sec_edgar/bulk/companyfacts.zip  # 첫 적재
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path: etl/·scripts/·src/)

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from wl_sqlite import connect_rw  # noqa: E402

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{y}/QTR{q}/form.{ymd}.idx"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FF_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Siccodes{n}.zip"
FF_SCHEMES = (5, 10, 12, 17, 30, 38, 48, 49)
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "us_facts.sqlite3"
RAW_ROOT = Path("D:/sec_edgar")
PERIODIC_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "10-KT", "10-KT/A",
                             "10-QT", "10-QT/A", "20-F", "20-F/A", "40-F", "40-F/A"})
DELAY = 0.12
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
OVERLAP_DAYS = 3
HOLIDAY_AFTER_DAYS = 4
COMMIT_EVERY = 200
SQLITE_INT_MAX = 2**63 - 1


class FetchError(Exception):
    """SEC 수신 실패 (재시도 소진·예상 외 상태)."""


def get_user_agent() -> str:
    """.env SEC_USER_AGENT 반환. 없으면 RuntimeError (값은 절대 출력하지 않는다)."""
    load_dotenv()
    ua = os.getenv("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(".env에 SEC_USER_AGENT가 없어")
    return ua


def http_get(session, url: str, ua: str) -> bytes | None:
    """200 → content, 404·S3 AccessDenied 403 → None. 429·403·5xx·통신오류는 지수 백오프 후 FetchError. 성공·실패 무관 호출 뒤 DELAY 휴식."""
    headers = {"User-Agent": ua}
    last_err: str | None = None
    try:
        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                last_err = type(e).__name__
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                break
            if resp.status_code == 200:
                return resp.content
            # SEC Archives(S3)는 없는 파일에 403 AccessDenied 를 준다(2026-10-11 실측). 요청과다 403 은 HTML 이라 재시도 유지.
            if resp.status_code == 404 or (resp.status_code == 403 and b"<Code>AccessDenied</Code>" in resp.content):
                return None
            last_err = str(resp.status_code)
            if resp.status_code in (429, 403) or resp.status_code >= 500:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                break
            raise FetchError(f"{url} HTTP {resp.status_code}")
        raise FetchError(f"{url} HTTP {last_err} (재시도 {MAX_RETRIES}회 소진)")
    finally:
        time.sleep(DELAY)


def fact_versions(facts: dict) -> list[tuple]:
    """(taxonomy, tag, unit, start, end)별 filed 순 버전 행.

    같은 filed는 accn 최대 1개만 대표로, 직전 대표 val과 다를 때만 행을 남긴다(첫 행은 항상).
    반환: (taxonomy, tag, label, unit, start, end, filed, val, accn, form, fy, fp).
    """
    groups: dict[tuple, list[dict]] = {}
    labels: dict[tuple, str | None] = {}
    for taxonomy, tags in sorted((facts.get("facts") or {}).items()):
        for tag, node in sorted((tags or {}).items()):
            labels[(taxonomy, tag)] = (node or {}).get("label")
            for unit, entries in sorted(((node or {}).get("units") or {}).items()):
                for e in entries or []:
                    if (e or {}).get("form") not in PERIODIC_FORMS:
                        continue
                    start = e.get("start") or ""
                    end = e.get("end") or ""
                    groups.setdefault((taxonomy, tag, unit, start, end), []).append(e)
    out: list[tuple] = []
    for taxonomy, tag, unit, start, end in sorted(groups):
        entries = sorted(groups[(taxonomy, tag, unit, start, end)],
                         key=lambda e: (e.get("filed", ""), e.get("accn", "")))
        reps: list[dict] = []
        for e in entries:
            if reps and reps[-1].get("filed") == e.get("filed"):
                reps[-1] = e  # 정렬済み라 뒤 accn이 더 큼
            else:
                reps.append(e)
        label = labels.get((taxonomy, tag))
        first = True
        prev_val = None
        for e in reps:
            val = e.get("val")
            if first or val != prev_val:
                out.append((taxonomy, tag, label, unit, start, end, e.get("filed"), val,
                            e.get("accn"), e.get("form"), e.get("fy"), e.get("fp")))
                prev_val = val
                first = False
    return out


_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    cik         TEXT PRIMARY KEY,
    entity_name TEXT,
    ticker      TEXT,
    exchange    TEXT,
    is_listed   INTEGER NOT NULL,
    raw_updated TEXT,
    updated_at  TEXT,
    sic         INTEGER,
    sic_desc    TEXT
);
CREATE TABLE IF NOT EXISTS ff_industries (
    scheme      INTEGER NOT NULL,
    ind_no      INTEGER NOT NULL,
    ind_short   TEXT NOT NULL,
    ind_name    TEXT,
    PRIMARY KEY (scheme, ind_no)
);
CREATE TABLE IF NOT EXISTS ff_sic_ranges (
    scheme      INTEGER NOT NULL,
    ind_no      INTEGER NOT NULL,
    sic_lo      INTEGER NOT NULL,
    sic_hi      INTEGER NOT NULL,
    sic_desc    TEXT,
    PRIMARY KEY (scheme, sic_lo, sic_hi)
);
CREATE TABLE IF NOT EXISTS concepts (
    concept_id  INTEGER PRIMARY KEY,
    taxonomy    TEXT NOT NULL,
    tag         TEXT NOT NULL,
    label       TEXT,
    UNIQUE (taxonomy, tag)
);
CREATE TABLE IF NOT EXISTS facts (
    cik         TEXT NOT NULL,
    concept_id  INTEGER NOT NULL,
    unit        TEXT NOT NULL,
    start       TEXT NOT NULL,
    end         TEXT NOT NULL,
    filed       TEXT NOT NULL,
    val         REAL NOT NULL,
    accn        TEXT NOT NULL,
    form        TEXT NOT NULL,
    fy          INTEGER,
    fp          TEXT,
    PRIMARY KEY (cik, concept_id, unit, start, end, filed)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS ix_facts_concept ON facts(concept_id, end);
"""


def ensure_schema(con) -> None:
    con.executescript(_SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(companies)")}
    if "sic" not in cols:
        con.execute("ALTER TABLE companies ADD COLUMN sic INTEGER")
    if "sic_desc" not in cols:
        con.execute("ALTER TABLE companies ADD COLUMN sic_desc TEXT")


def _sql_val(v):
    # SQLite INTEGER 범위 밖(공시 단위 오기)은 REAL 로.
    return float(v) if isinstance(v, int) and abs(v) > SQLITE_INT_MAX else v


def replace_company(con, cik10: str, facts: dict, now: str) -> int:
    """그 회사 facts 전량 교체 + companies upsert(기존 ticker·exchange·is_listed 유지). commit은 호출자."""
    versions = fact_versions(facts)
    pairs: dict[tuple, str | None] = {}
    for taxonomy, tag, label, *_rest in versions:
        pairs.setdefault((taxonomy, tag), label)
    for (taxonomy, tag), label in pairs.items():
        con.execute("INSERT OR IGNORE INTO concepts(taxonomy, tag, label) VALUES (?,?,?)",
                    (taxonomy, tag, label))
    cmap: dict[tuple, int] = {}
    for taxonomy, tag in pairs:
        row = con.execute("SELECT concept_id FROM concepts WHERE taxonomy=? AND tag=?",
                          (taxonomy, tag)).fetchone()
        cmap[(taxonomy, tag)] = row[0]
    con.execute("DELETE FROM facts WHERE cik=?", (cik10,))
    con.executemany(
        "INSERT INTO facts(cik, concept_id, unit, start, end, filed, val, accn, form, fy, fp)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(cik10, cmap[(taxonomy, tag)], unit, start, end, filed, _sql_val(val), accn, form, fy, fp)
         for taxonomy, tag, _label, unit, start, end, filed, val, accn, form, fy, fp in versions],
    )
    entity_name = facts.get("entityName")
    con.execute("INSERT OR IGNORE INTO companies(cik, entity_name, is_listed, raw_updated, updated_at)"
                " VALUES (?,?,?,?,?)", (cik10, entity_name, 0, now, now))
    con.execute("UPDATE companies SET entity_name=?, raw_updated=?, updated_at=? WHERE cik=?",
                (entity_name, now, now, cik10))
    return len(versions)


def raw_path(raw_root: Path, cik10: str) -> Path:
    return raw_root / "companyfacts" / f"CIK{cik10}.json.gz"


def _atomic_gzip_write(path: Path, data: bytes) -> None:
    """같은 폴더 .tmp에 gzip 쓰고 os.replace (쓰다 죽어도 이전 파일 유지)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    with gzip.open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def save_raw(raw_root: Path, cik10: str, data: bytes) -> None:
    _atomic_gzip_write(raw_path(raw_root, cik10), data)


def index_path(raw_root: Path, d: date) -> Path:
    return raw_root / "daily_index" / f"{d.year}" / f"QTR{(d.month - 1) // 3 + 1}" / f"form.{d:%Y%m%d}.idx.gz"


def save_index(raw_root: Path, d: date, data: bytes) -> None:
    _atomic_gzip_write(index_path(raw_root, d), data)


def parse_daily_index(text: str) -> set[str]:
    """구분선(-----…) 다음 줄부터 form이 정기보고서인 행의 CIK(10자리) 집합."""
    out: set[str] = set()
    started = False
    for line in text.splitlines():
        if not started:
            if line.startswith("-----"):
                started = True
            continue
        tokens = line.split()
        if len(tokens) >= 5 and tokens[0] in PERIODIC_FORMS:
            try:
                out.add(str(int(tokens[-3])).zfill(10))
            except ValueError:
                continue
    return out


def submissions_path(raw_root: Path, cik10: str) -> Path:
    return raw_root / "submissions" / f"CIK{cik10}.json.gz"


def save_submissions(raw_root: Path, cik10: str, data: bytes) -> None:
    _atomic_gzip_write(submissions_path(raw_root, cik10), data)


def ff_path(raw_root: Path, n: int) -> Path:
    return raw_root / "ff_industry" / f"Siccodes{n}.zip"


def save_ff(raw_root: Path, n: int, data: bytes) -> None:
    path = ff_path(raw_root, n)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def parse_ff_siccodes(text: str) -> tuple[list[tuple], list[tuple]]:
    industries: list[tuple] = []
    ranges: list[tuple] = []
    current: int | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        m = re.match(r"^\s*(\d{4})-(\d{4})\s*(.*?)\s*$", line)
        if m:
            if current is not None:
                ranges.append((current, int(m.group(1)), int(m.group(2)), m.group(3) or None))
            continue
        m = re.match(r"^\s*(\d+)\s+(\S+)\s+(.*\S)?\s*$", line)
        if m:
            current = int(m.group(1))
            industries.append((current, m.group(2), m.group(3) or None))
    return (industries, ranges)


def load_ff(con, raw_root: Path, get_bytes) -> int:
    total = 0
    for n in FF_SCHEMES:
        url = FF_URL.format(n=n)
        data = get_bytes(url)
        if data is None:
            raise FetchError(f"{url} HTTP 404")
        save_ff(raw_root, n, data)
        with zipfile.ZipFile(ff_path(raw_root, n)) as zf:
            text = zf.read(zf.namelist()[0]).decode("latin-1")
        industries, ranges = parse_ff_siccodes(text)
        con.execute("DELETE FROM ff_industries WHERE scheme=?", (n,))
        con.execute("DELETE FROM ff_sic_ranges WHERE scheme=?", (n,))
        con.executemany("INSERT INTO ff_industries(scheme, ind_no, ind_short, ind_name) VALUES (?,?,?,?)",
                        [(n, ind_no, short, name) for ind_no, short, name in industries])
        con.executemany("INSERT OR IGNORE INTO ff_sic_ranges(scheme, ind_no, sic_lo, sic_hi, sic_desc)"
                        " VALUES (?,?,?,?,?)",
                        [(n, ind_no, lo, hi, desc) for ind_no, lo, hi, desc in ranges])
        total += len(ranges)
    con.commit()
    return total


def ff_industry(con, scheme: int, sic: int | None) -> tuple[int, str] | None:
    if sic is None:
        return None
    row = con.execute(
        "SELECT r.ind_no, i.ind_short FROM ff_sic_ranges r"
        " JOIN ff_industries i ON i.scheme=r.scheme AND i.ind_no=r.ind_no"
        " WHERE r.scheme=? AND r.sic_lo <= ? AND ? <= r.sic_hi"
        " ORDER BY r.ind_no LIMIT 1",
        (scheme, sic, sic)).fetchone()
    if row is not None:
        return (row[0], row[1])
    row = con.execute(
        "SELECT i.ind_no, i.ind_short FROM ff_industries i"
        " WHERE i.scheme=? AND NOT EXISTS (SELECT 1 FROM ff_sic_ranges r"
        " WHERE r.scheme=i.scheme AND r.ind_no=i.ind_no)"
        " ORDER BY i.ind_no LIMIT 1",
        (scheme,)).fetchone()
    if row is not None:
        return (row[0], row[1])
    return None


def load_sic(con, raw_root: Path, get_bytes, ciks, now: str) -> tuple[int, int]:
    updated = not_found = done = 0
    for cik10 in ciks:
        data = get_bytes(SUBMISSIONS_URL.format(cik=cik10))
        done += 1
        if data is None:
            not_found += 1
        else:
            save_submissions(raw_root, cik10, data)
            j = json.loads(data)
            raw_sic = j.get("sic")
            sic = None
            if isinstance(raw_sic, int) and not isinstance(raw_sic, bool):
                sic = raw_sic
            elif isinstance(raw_sic, str):
                s = raw_sic.strip()
                if s.isdigit():
                    sic = int(s)
            raw_desc = j.get("sicDescription")
            desc = raw_desc.strip() if isinstance(raw_desc, str) else None
            if not desc:
                desc = None
            con.execute("UPDATE companies SET sic=?, sic_desc=?, updated_at=? WHERE cik=?",
                        (sic, desc, now, cik10))
            updated += 1
        if done % COMMIT_EVERY == 0:
            con.commit()
    con.commit()
    return (updated, not_found)


def refresh_companies(con, payload: dict, now: str) -> None:
    """company_tickers_exchange.json으로 ticker·exchange·is_listed 갱신. 없는 회사는 is_listed=0."""
    fields = payload["fields"]
    ic, inm, it, ie = (fields.index(k) for k in ("cik", "name", "ticker", "exchange"))
    con.execute("UPDATE companies SET is_listed=0, ticker=NULL, exchange=NULL")
    seen: set[str] = set()
    for row in payload["data"]:
        cik10 = str(int(row[ic])).zfill(10)
        if cik10 in seen:
            continue
        seen.add(cik10)
        name, ticker, exch = row[inm], row[it], row[ie]
        cur = con.execute("SELECT cik FROM companies WHERE cik=?", (cik10,)).fetchone()
        if cur is None:
            con.execute("INSERT INTO companies(cik, entity_name, ticker, exchange, is_listed, updated_at)"
                        " VALUES (?,?,?,?,1,?)", (cik10, name, ticker, exch, now))
        else:
            con.execute("UPDATE companies SET ticker=?, exchange=?, is_listed=1, updated_at=? WHERE cik=?",
                        (ticker, exch, now, cik10))


def get_state(con, key) -> str | None:
    row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(con, key, value) -> None:
    con.execute("INSERT OR REPLACE INTO state(key, value) VALUES (?,?)", (key, value))


def init_from_zip(con, zip_path: Path, raw_root: Path, as_of: date, now: str) -> tuple[int, int]:
    """companyfacts.zip 전량 첫 적재. 반환 (회사 수, 행 수)."""
    n = rows = 0
    with zipfile.ZipFile(zip_path) as zf:
        names = [nm for nm in zf.namelist()
                 if nm.startswith("CIK") and nm.endswith(".json") and nm[3:-5].isdigit()]
        for name in names:
            cik10 = name[3:-5]
            data = zf.read(name)
            save_raw(raw_root, cik10, data)
            rows += replace_company(con, cik10, json.loads(data), now)
            n += 1
            if n % COMMIT_EVERY == 0:
                con.commit()
            if n % 1000 == 0:
                print(f"[{n}] rows={rows}")
    set_state(con, "last_index_date", (as_of - timedelta(days=3)).isoformat())
    con.commit()
    return (n, rows)


def target_dates(last: date, today: date) -> list[date]:
    """last+1 ~ today-1. 오름차순."""
    out: list[date] = []
    d = last + timedelta(days=1)
    while d <= today - timedelta(days=1):
        out.append(d)
        d += timedelta(days=1)
    return out


def _read_saved_index(path: Path) -> set[str]:
    with gzip.open(path, "rb") as f:
        return parse_daily_index(f.read().decode("utf-8", errors="replace"))


def update(con, raw_root: Path, today: date, get_bytes, now: str) -> dict:
    """일일 갱신. 전부 성공해야 last_index_date 전진. FetchError는 그대로 올려보낸다(상태 미전진)."""
    last_raw = get_state(con, "last_index_date")
    if last_raw is None:
        raise RuntimeError("--init-from-zip 먼저")
    last = date.fromisoformat(last_raw)
    dates = target_dates(last, today)
    cutoff = today - timedelta(days=HOLIDAY_AFTER_DAYS)
    ciks: set[str] = set()
    received: list[date] = []
    skipped = 0
    processed_last = last
    for d in dates:
        url = INDEX_URL.format(y=d.year, q=(d.month - 1) // 3 + 1, ymd=d.strftime("%Y%m%d"))
        data = get_bytes(url)
        if data is None:
            if d.weekday() >= 5 or d <= cutoff:
                skipped += 1
                continue
            break
        save_index(raw_root, d, data)
        ciks |= parse_daily_index(data.decode("utf-8", errors="replace"))
        received.append(d)
        processed_last = d
    if dates:
        first = dates[0]
        for i in range(1, OVERLAP_DAYS + 1):
            p = index_path(raw_root, first - timedelta(days=i))
            if p.exists():
                ciks |= _read_saved_index(p)
    updated = not_found = rows = done = 0
    ok_ciks: list[str] = []
    for cik10 in sorted(ciks):
        data = get_bytes(FACTS_URL.format(cik=cik10))
        done += 1
        if data is None:
            not_found += 1
        else:
            save_raw(raw_root, cik10, data)
            rows += replace_company(con, cik10, json.loads(data), now)
            updated += 1
            ok_ciks.append(cik10)
        if done % COMMIT_EVERY == 0:
            con.commit()
    con.commit()
    sic_updated, _ = load_sic(con, raw_root, get_bytes, ok_ciks, now)
    if received:
        set_state(con, "last_index_date", processed_last.isoformat())
        con.commit()
    return {"dates": len(received), "skipped": skipped, "ciks": len(ciks),
            "updated": updated, "not_found": not_found, "rows": rows, "sic": sic_updated}


def value_as_of(con, cik10, taxonomy, tag, unit, start, end, as_of: str) -> float | None:
    """filed <= as_of 중 filed 최대 행의 val. 없으면 None."""
    row = con.execute(
        "SELECT f.val FROM facts f JOIN concepts c USING (concept_id)"
        " WHERE f.cik=? AND c.taxonomy=? AND c.tag=? AND f.unit=? AND f.start=? AND f.end=?"
        " AND f.filed <= ? ORDER BY f.filed DESC LIMIT 1",
        (cik10, taxonomy, tag, unit, start, end, as_of)).fetchone()
    return row[0] if row else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    p = argparse.ArgumentParser(description="SEC XBRL 재무 원본 보관 + us_facts.sqlite3 갱신")
    p.add_argument("--init-from-zip", type=Path, default=None, help="첫 적재용 companyfacts.zip 경로")
    p.add_argument("--as-of", type=str, default=None, help="init 기준일 YYYY-MM-DD (기본 zip 수정일)")
    p.add_argument("--load-ff", action="store_true", help="FF 업종 분류만 적재")
    p.add_argument("--load-sic", action="store_true", help="SIC만 적재")
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    p.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    args = p.parse_args()

    raw_root = Path(args.raw_root)
    if not raw_root.exists():
        print(f"raw_root 없음(D: 미연결?): {raw_root}", file=sys.stderr)
        sys.exit(1)

    try:
        ua = get_user_agent()
        session = requests.Session()

        def get_bytes(url: str) -> bytes | None:
            return http_get(session, url, ua)

        db_path = Path(args.db)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        now = _now()
        with connect_rw(db_path) as con:
            ensure_schema(con)
            if args.load_ff or args.load_sic:
                if args.load_ff:
                    n = load_ff(con, raw_root, get_bytes)
                    print(f"us_facts FF 분류: 범위 {n}")
                if args.load_sic:
                    ciks = [r[0] for r in con.execute("SELECT cik FROM companies ORDER BY cik")]
                    u, nf = load_sic(con, raw_root, get_bytes, ciks, now)
                    print(f"us_facts SIC: 반영 {u} 404 {nf}")
            elif args.init_from_zip is not None:
                zip_path = Path(args.init_from_zip)
                if args.as_of:
                    as_of = date.fromisoformat(args.as_of)
                else:
                    as_of = date.fromtimestamp(zip_path.stat().st_mtime)
                n, rows = init_from_zip(con, zip_path, raw_root, as_of, now)
                tick = get_bytes(TICKERS_URL)
                if tick is None:
                    raise FetchError(f"{TICKERS_URL} HTTP 404")
                refresh_companies(con, json.loads(tick), now)
                con.commit()
                print(f"us_facts 첫적재: 회사 {n} 행 {rows}")
            else:
                tick = get_bytes(TICKERS_URL)
                if tick is None:
                    raise FetchError(f"{TICKERS_URL} HTTP 404")
                refresh_companies(con, json.loads(tick), now)
                con.commit()
                r = update(con, raw_root, date.today(), get_bytes, now)
                print(f"us_facts 갱신: 날짜 {r['dates']} 휴일넘김 {r['skipped']} 대상 {r['ciks']}"
                      f" 반영 {r['updated']} 404 {r['not_found']} 행 {r['rows']} SIC {r['sic']}")
    except (FetchError, RuntimeError) as e:
        print(f"us_facts 실패: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
