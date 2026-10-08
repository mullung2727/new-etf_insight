"""미국 상장사 SEC EDGAR CompanyFacts 재무제표 적재 배치.

목표: Nasdaq/NYSE/CBOE 상장사 재무제표를 sqlite(db/us_financials.sqlite3)에 쌓아
한국 지표 DB(financial_indicators.sqlite3)와 동형으로 조회·랭킹한다.

회사당 1콜로 전 기간·전 개념을 받는다: start/end로 계산한 frame으로
연간(10-K)은 CY{YYYY}(flow) / CY{YYYY}Q[1-4]I(stock),
분기(10-Q)는 CY{YYYY}Q{q}[/I] 정확일치 + filed 최신 채택.
Q4는 FY-(Q1+Q2+Q3) 파생. SEC_USER_AGENT(.env) 필수.

Usage (from etl/):
    uv run python scripts/build_us_financials.py --self-check   # AAPL·JPM·VZ known-value
    uv run python scripts/build_us_financials.py --limit 5      # 소량 적재
    uv run python scripts/build_us_financials.py                # 전체
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path: etl/·scripts/·src/)

import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from wl_sqlite import connect_rw  # noqa: E402

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "us_financials.sqlite3"
TICKERS_CACHE = Path(__file__).resolve().parents[1] / "db" / "us_tickers.json"
TICKERS_TTL_SEC = 24 * 3600
EXCHANGES = ("Nasdaq", "NYSE", "CBOE")
DELAY = 0.12          # sec / 호출. SEC 공식 한도 초당 10회
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3       # 429·403·5xx 지수 백오프
COMMIT_EVERY = 50     # cik 단위 commit 주기
ANNUAL_YEARS = 17
QUARTERS = 71
ANNUAL_FORMS = ("10-K", "10-K/A")
QUARTER_FORMS = ("10-Q", "10-Q/A")

# (account_nm, sj_div, kind, concepts). kind: flow=기간 귀속 / stock=기말 잔액.
# 개념 폴백체인은 기간마다 독립 적용 (순서대로 첫 적중).
ACCOUNT_CHAINS = (
    ("매출액", "IS", "flow", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax")),
    ("영업이익", "IS", "flow", ("OperatingIncomeLoss",)),
    ("당기순이익", "IS", "flow", ("NetIncomeLoss",)),
    ("자산총계", "BS", "stock", ("Assets",)),
    ("부채총계", "BS", "stock", ("Liabilities",)),
    ("자본총계", "BS", "stock", ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity")),
    ("이익잉여금", "BS", "stock", ("RetainedEarningsAccumulatedDeficit",)),
    ("비지배지분", "BS", "stock", ("MinorityInterest",)),
    ("보통주자본", "BS", "stock", ("CommonStockValue",)),
)
ACCOUNT_SJ = {nm: sj for nm, sj, _kind, _concepts in ACCOUNT_CHAINS}
ACCOUNT_KIND = {nm: kind for nm, _sj, kind, _concepts in ACCOUNT_CHAINS}

RATIO_DEFS = (
    ("ROE", "ROE"),
    ("DEBT_RATIO", "부채비율"),
    ("REV_GROWTH", "매출증가율"),
    ("NET_MARGIN", "순이익률"),
    ("OP_MARGIN", "영업이익률"),
)


class FetchError(Exception):
    """CompanyFacts 수집 실패 (재시도 소진·예상 외 상태)."""


def get_user_agent() -> str:
    """.env SEC_USER_AGENT 반환. 없으면 RuntimeError (값은 절대 출력하지 않는다)."""
    load_dotenv()
    ua = os.getenv("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(".env에 SEC_USER_AGENT가 없어")
    return ua


# ── 기간·유니버스 ───────────────────────────────────────────────────────────────

def plan_periods(today: date) -> tuple[list[str], list[tuple[str, int]]]:
    """연간 5개(직전 완결 연도까지) + 분기 8개(직전 완결 분기까지). 둘 다 오름차순."""
    years = [str(y) for y in range(today.year - ANNUAL_YEARS, today.year)]
    y, q = today.year, (today.month - 1) // 3  # 이번 분기 − 1 (0이면 전년 Q4)
    if q == 0:
        y, q = y - 1, 4
    quarters: list[tuple[str, int]] = []
    for _ in range(QUARTERS):
        quarters.append((str(y), q))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    quarters.reverse()
    return years, quarters


def filter_universe(payload: dict) -> list[tuple[str, str, str, str]]:
    """tickers_exchange payload → (cik10, ticker, name, exchange). 거래소 필터, cik 중복은 첫 ticker만."""
    fields = payload["fields"]
    ic, inm, it, ie = (fields.index(k) for k in ("cik", "name", "ticker", "exchange"))
    seen: set[str] = set()
    out: list[tuple[str, str, str, str]] = []
    for row in payload["data"]:
        if row[ie] not in EXCHANGES:
            continue
        cik10 = str(int(row[ic])).zfill(10)
        if cik10 in seen:  # GOOGL/GOOG 등 동일 cik 다중 ticker
            continue
        seen.add(cik10)
        out.append((cik10, str(row[it]), str(row[inm]), str(row[ie])))
    return out


def load_universe(session, ua: str, cache_path: Path = TICKERS_CACHE) -> list[tuple[str, str, str, str]]:
    """유니버스 목록. 캐시가 TTL 이내면 재사용, 아니면 받아서 캐시."""
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < TICKERS_TTL_SEC:
        return filter_universe(json.loads(cache_path.read_text(encoding="utf-8")))
    resp = session.get(TICKERS_URL, headers={"User-Agent": ua}, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return filter_universe(payload)


def fetch_facts(cik10: str, session, ua: str) -> dict | None:
    """회사당 1콜로 전 기간·전 개념 수신. 404 → None(자료 없음)."""
    headers = {"User-Agent": ua}
    last_err: str | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(FACTS_URL.format(cik=cik10), headers=headers, timeout=REQUEST_TIMEOUT)
        except (requests.RequestException, ValueError) as e:
            last_err = type(e).__name__
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            break
        if resp.status_code == 200:
            try:
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                last_err = type(e).__name__
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    continue
                break
        if resp.status_code == 404:
            return None
        last_err = str(resp.status_code)
        if resp.status_code in (429, 403) or resp.status_code >= 500:
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
                continue
            break
        raise FetchError(f"{cik10} CompanyFacts HTTP {resp.status_code}")
    raise FetchError(f"{cik10} CompanyFacts HTTP {last_err} (재시도 {MAX_RETRIES}회 소진)")


# ── 추출 ──────────────────────────────────────────────────────────────────────

def derive_frame(entry: dict) -> str | None:
    """start/end로 frame 계산. SEC 원본 frame은 같은 기간 fact 중 1건에만 붙어 못 쓴다. 기간 밖·파싱 실패면 None."""
    try:
        end = date.fromisoformat(entry.get("end") or "")
    except ValueError:
        return None
    if entry.get("start"):  # flow
        try:
            start = date.fromisoformat(entry["start"])
        except ValueError:
            return None
        days = (end - start).days
        mid = start + (end - start) / 2
        if 335 <= days <= 395:
            return f"CY{mid.year}"
        if 61 <= days <= 121:
            return f"CY{mid.year}Q{(mid.month - 1) // 3 + 1}"
        return None
    cands = [((end.year - 1, 4), date(end.year - 1, 12, 31)),
             ((end.year, 1), date(end.year, 3, 31)),
             ((end.year, 2), date(end.year, 6, 30)),
             ((end.year, 3), date(end.year, 9, 30)),
             ((end.year, 4), date(end.year, 12, 31))]
    (y, q), qd = min(cands, key=lambda t: abs((end - t[1]).days))
    if abs((end - qd).days) > 35:
        return None
    return f"CY{y}Q{q}I"


def pick_fact(entries: list, forms: tuple, frame_pred) -> dict | None:
    """form ∈ forms 이고 frame_pred(frame)인 것 중 (end, filed) 최대. 계산 frame 없는 원소 제외."""
    best = None
    for e in entries or []:
        frame = derive_frame(e)
        if frame is None or e.get("form") not in forms or not frame_pred(frame):
            continue
        key = (e.get("end", ""), e.get("filed", ""))
        if best is None or key > (best.get("end", ""), best.get("filed", "")):
            best = e
    return best


def _concept_entries(facts: dict, concept: str) -> list:
    """facts["facts"]["us-gaap"][concept]["units"]["USD"]. 키 없으면 [] (미적중)."""
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    units = (gaap.get(concept) or {}).get("units") or {}
    return units.get("USD") or []


def _pick_chain(facts: dict, concepts: tuple, forms: tuple, frame_pred) -> tuple:
    """개념 체인 순서대로 첫 적중 → (amount, filed). 전멸이면 (None, None)."""
    for concept in concepts:
        hit = pick_fact(_concept_entries(facts, concept), forms, frame_pred)
        if hit is not None:
            return (hit.get("val"), hit.get("filed"))
    return (None, None)


def _fallback_liabilities(facts: dict, row: dict, forms: tuple, frame_pred) -> None:
    """Liabilities 미태깅 보완: LSE−자본총계를 부채총계로. 값 있을 때만 덮어쓴다."""
    if row["부채총계"][0] is not None or row["자본총계"][0] is None:
        return
    lse, filed = _pick_chain(facts, ("LiabilitiesAndStockholdersEquity",), forms, frame_pred)
    # 자본총계가 NCI 포함이면 LSE−자본총계에 메자닌(임시자본)이 부채로 섞일 수 있음.
    if lse is not None:
        row["부채총계"] = (lse - row["자본총계"][0], filed)


def extract_accounts(facts: dict, years: list, quarters: list) -> dict:
    """{(bsns_year, reprt_code): {account_nm: (amount, filed)}}. Q4 제외. 9개 전멸 기간은 키 없음."""
    out: dict = {}
    for y in years:
        stock_match = re.compile(f"^CY{y}Q[1-4]I$").match
        row = {}
        for account_nm, _sj, kind, concepts in ACCOUNT_CHAINS:
            pred = (lambda f, _y=y: f == f"CY{_y}") if kind == "flow" else stock_match
            row[account_nm] = _pick_chain(facts, concepts, ANNUAL_FORMS, pred)
        _fallback_liabilities(facts, row, ANNUAL_FORMS, stock_match)
        if any(v is not None for v, _f in row.values()):
            out[(y, "FY")] = row
    for y, q in quarters:
        if q == 4:
            continue  # derive_q4에서 파생
        row = {}
        for account_nm, _sj, kind, concepts in ACCOUNT_CHAINS:
            want = f"CY{y}Q{q}" if kind == "flow" else f"CY{y}Q{q}I"
            row[account_nm] = _pick_chain(facts, concepts, QUARTER_FORMS, lambda f, _w=want: f == _w)
        _fallback_liabilities(facts, row, QUARTER_FORMS, lambda f, _w=f"CY{y}Q{q}I": f == _w)
        if any(v is not None for v, _f in row.values()):
            out[(y, f"Q{q}")] = row
    return out


def derive_q4(periods_map: dict, quarters: list) -> None:
    """in-place. (Y,4) ∈ quarters 이고 FY Y가 있을 때만 Q4 생성. flow=FY−합, stock=FY값, filed=FY filed."""
    for y in sorted({yy for yy, qq in quarters if qq == 4}):
        fy = periods_map.get((y, "FY"))
        if fy is None:
            continue
        qs = [periods_map.get((y, f"Q{q}"), {}) for q in (1, 2, 3)]
        row = {}
        for account_nm in ACCOUNT_SJ:
            famt, ffiled = fy.get(account_nm, (None, None))
            if ACCOUNT_KIND[account_nm] == "flow":
                vals = [q.get(account_nm, (None, None))[0] for q in qs]
                if famt is not None and all(v is not None for v in vals):
                    row[account_nm] = (famt - sum(vals), ffiled)
                else:
                    row[account_nm] = (None, ffiled)
            else:
                row[account_nm] = (famt, ffiled)
        if any(v is not None for v, _f in row.values()):
            periods_map[(y, "Q4")] = row


# ── 비율 (broker-web/lib/dart.ts computeRatio와 동일 정의) ───────────────────────

def _amt(periods_map: dict, key: tuple, account_nm: str):
    row = periods_map.get(key)
    if row is None:
        return None
    return row.get(account_nm, (None, None))[0]


def compute_ratios(periods_map: dict) -> list:
    """[(bsns_year, reprt_code, idx_code, idx_nm, idx_val)]. None 지표는 행 없음."""
    out: list = []
    for y, r in sorted(periods_map):
        rev = _amt(periods_map, (y, r), "매출액")
        op = _amt(periods_map, (y, r), "영업이익")
        net = _amt(periods_map, (y, r), "당기순이익")
        liab = _amt(periods_map, (y, r), "부채총계")
        eq = _amt(periods_map, (y, r), "자본총계")
        vals: dict = {}
        if r == "FY":  # 연간 전용 4지표
            eq_prev = _amt(periods_map, (str(int(y) - 1), "FY"), "자본총계")
            rev_prev = _amt(periods_map, (str(int(y) - 1), "FY"), "매출액")
            vals["DEBT_RATIO"] = round(liab / eq * 100, 2) if (liab is not None and eq) else None
            if net is None or not eq:
                vals["ROE"] = None
            else:
                avg = (eq + eq_prev) / 2 if eq_prev is not None else eq
                vals["ROE"] = round(net / avg * 100, 2) if avg else None
            vals["REV_GROWTH"] = round((rev / rev_prev - 1) * 100, 2) if (rev is not None and rev_prev) else None
            vals["NET_MARGIN"] = round(net / rev * 100, 2) if (net is not None and rev) else None
        vals["OP_MARGIN"] = round(op / rev * 100, 2) if (op is not None and rev) else None
        for code, nm in RATIO_DEFS:
            if vals.get(code) is not None:
                out.append((y, r, code, nm, vals[code]))
    return out


# ── DB ────────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corps (
    cik         TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL,
    corp_name   TEXT NOT NULL,
    exchange    TEXT,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    cik         TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    sj_div      TEXT,
    account_nm  TEXT NOT NULL,
    amount      REAL,
    ticker      TEXT,
    filed_dt    TEXT,
    currency    TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (cik, bsns_year, reprt_code, account_nm)
);
CREATE INDEX IF NOT EXISTS ix_acnt_rank ON accounts(account_nm, bsns_year, reprt_code, amount);
CREATE TABLE IF NOT EXISTS indicators (
    cik         TEXT NOT NULL,
    bsns_year   TEXT NOT NULL,
    reprt_code  TEXT NOT NULL,
    idx_code    TEXT NOT NULL,
    idx_nm      TEXT,
    idx_val     REAL,
    ticker      TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (cik, bsns_year, reprt_code, idx_code)
);
CREATE INDEX IF NOT EXISTS ix_ind_rank ON indicators(idx_code, bsns_year, reprt_code, idx_val);
CREATE VIEW IF NOT EXISTS v_key_indicators AS
SELECT i.cik, c.corp_name, i.ticker, i.bsns_year, i.reprt_code,
       MAX(CASE WHEN i.idx_code='ROE' THEN i.idx_val END) AS roe,
       MAX(CASE WHEN i.idx_code='DEBT_RATIO' THEN i.idx_val END) AS debt_ratio,
       MAX(CASE WHEN i.idx_code='REV_GROWTH' THEN i.idx_val END) AS revenue_growth,
       MAX(CASE WHEN i.idx_code='NET_MARGIN' THEN i.idx_val END) AS net_margin,
       MAX(CASE WHEN i.idx_code='OP_MARGIN' THEN i.idx_val END) AS op_margin
FROM indicators i JOIN corps c USING(cik)
GROUP BY i.cik, i.bsns_year, i.reprt_code;
CREATE VIEW IF NOT EXISTS v_key_accounts AS
SELECT a.cik, c.corp_name, a.ticker, a.bsns_year, a.reprt_code,
       MAX(CASE WHEN a.account_nm='매출액' THEN a.amount END) AS revenue,
       MAX(CASE WHEN a.account_nm='영업이익' THEN a.amount END) AS op_profit,
       MAX(CASE WHEN a.account_nm='당기순이익' THEN a.amount END) AS net_income,
       MAX(CASE WHEN a.account_nm='자산총계' THEN a.amount END) AS total_assets,
       CASE WHEN MAX(CASE WHEN a.account_nm='매출액' THEN a.amount END) > 0
            THEN round(100.0 * MAX(CASE WHEN a.account_nm='영업이익' THEN a.amount END)
                             / MAX(CASE WHEN a.account_nm='매출액' THEN a.amount END), 2)
       END AS op_margin
FROM accounts a JOIN corps c USING(cik)
GROUP BY a.cik, a.bsns_year, a.reprt_code;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(con) -> None:
    con.executescript(_SCHEMA)


def upsert_corps(con, universe: list) -> None:
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO corps VALUES (?,?,?,?,?)",
        [(cik, ticker, name, exch, now) for cik, ticker, name, exch in universe],
    )


def upsert_accounts(con, cik: str, ticker: str, periods_map: dict) -> int:
    now = _now()
    rows = [
        (cik, y, r, ACCOUNT_SJ[nm], nm, amount, ticker, filed, "USD", now)
        for (y, r), accts in sorted(periods_map.items())
        for nm, (amount, filed) in accts.items()
    ]
    con.executemany("INSERT OR REPLACE INTO accounts VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def upsert_indicators(con, cik: str, ticker: str, ratios: list) -> int:
    now = _now()
    con.executemany(
        "INSERT OR REPLACE INTO indicators VALUES (?,?,?,?,?,?,?,?)",
        [(cik, y, r, code, nm, val, ticker, now) for y, r, code, nm, val in ratios],
    )
    return len(ratios)


def existing_ciks(con, years: list, quarters: list) -> set[str]:
    """대상 기간 중 하나라도 accounts 행이 있는 cik. 이어받기 skip 기준."""
    periods = [(y, "FY") for y in years] + [(y, f"Q{q}") for y, q in quarters]
    if not periods:
        return set()
    ph = ",".join(["(?,?)"] * len(periods))
    params = [x for p in periods for x in p]
    cur = con.execute(
        f"SELECT DISTINCT cik FROM accounts WHERE (bsns_year, reprt_code) IN ({ph})",
        params,
    )
    return {row[0] for row in cur}


# ── 러너 ──────────────────────────────────────────────────────────────────────

def run(
    db_path: Path = DEFAULT_DB_PATH,
    limit: int | None = None,
    force: bool = False,
    today: date | None = None,
    universe: list | None = None,
    fetch_fn=None,
    session=None,
    ua: str | None = None,
) -> dict:
    """유니버스 적재: skip → fetch → 추출·Q4·비율 → upsert → 주기 commit. universe/fetch_fn 주입 시 네트워크 없음."""
    today = today or date.today()
    years, quarters = plan_periods(today)
    real_fetch = fetch_fn is None
    if universe is None or real_fetch:
        ua = ua or get_user_agent()
        if session is None:
            session = requests.Session()
    if universe is None:
        universe = load_universe(session, ua)
    if fetch_fn is None:
        def fetch(cik):
            return fetch_facts(cik, session, ua)
    else:
        fetch = fetch_fn

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    no_facts: list = []
    failed: list = []
    account_rows = indicator_rows = loaded = 0
    with connect_rw(db_path) as con:
        ensure_schema(con)
        upsert_corps(con, universe)
        con.commit()
        done = set() if force else existing_ciks(con, years, quarters)
        targets = [u for u in universe if u[0] not in done]
        skipped = len(universe) - len(targets)
        if limit is not None:
            targets = targets[:limit]
        n = len(targets)
        for i, (cik, ticker, _name, _exch) in enumerate(targets, 1):
            try:
                facts = fetch(cik)
            except FetchError:
                failed.append(cik)
                if real_fetch:
                    time.sleep(DELAY)
                continue
            if real_fetch:
                time.sleep(DELAY)
            if facts is None:
                no_facts.append(cik)
                continue
            periods_map = extract_accounts(facts, years, quarters)
            derive_q4(periods_map, quarters)
            account_rows += upsert_accounts(con, cik, ticker, periods_map)
            indicator_rows += upsert_indicators(con, cik, ticker, compute_ratios(periods_map))
            loaded += 1
            if i % COMMIT_EVERY == 0:
                con.commit()
                print(f"[{i}/{n}] rows={account_rows + indicator_rows}")
    print(f"failed {len(failed)} no_facts {len(no_facts)}")
    return {
        "universe": len(universe), "targets": len(targets), "skipped": skipped,
        "loaded": loaded, "no_facts": no_facts, "failed": failed,
        "account_rows": account_rows, "indicator_rows": indicator_rows,
    }


def _self_check() -> None:
    """AAPL·JPM·VZ known-value 실호출 검증."""
    ua = get_user_agent()
    session = requests.Session()
    years, quarters = ["2024"], []

    def fy(cik):
        facts = fetch_facts(cik, session, ua)
        time.sleep(DELAY)
        assert facts, f"{cik} facts 없음"
        got = extract_accounts(facts, years, quarters)
        assert ("2024", "FY") in got, f"{cik} FY2024 없음"
        return got[("2024", "FY")]

    rev = fy("0000320193")["매출액"][0]
    assert rev == 391035000000, f"AAPL FY2024 매출 틀림: {rev}"
    jpm = fy("0000019617")
    assert jpm["영업이익"][0] is None, f"JPM 영업이익은 None이어야: {jpm['영업이익']}"
    assert jpm["당기순이익"][0] is not None, "JPM 당기순이익 없음"
    nci = fy("0000732712")["비지배지분"][0]
    assert nci is not None, "VZ 비지배지분 없음"
    print(f"AAPL FY2024 매출={rev} JPM 순이익={jpm['당기순이익'][0]} VZ NCI={nci}")
    print("self-check PASS")


def main() -> None:
    p = argparse.ArgumentParser(description="미국 상장사 SEC EDGAR 재무제표 적재")
    p.add_argument("--self-check", action="store_true", help="AAPL·JPM·VZ known-value 검증만")
    p.add_argument("--limit", type=int, default=None, help="남은 대상 앞 N종목만")
    p.add_argument("--force", action="store_true", help="skip 무시 전량 재적재")
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = p.parse_args()

    if args.self_check:
        _self_check()
        return

    stats = run(db_path=args.db, limit=args.limit, force=args.force)
    out = dict(stats)
    out["no_facts"] = len(stats["no_facts"])
    out["failed"] = len(stats["failed"])
    print("DONE", out)


if __name__ == "__main__":
    main()
