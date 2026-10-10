"""DART 실적 공시(잠정실적·매출/손익 30% 변동·사업보고서) 적재 배치.

"실적이 시장에 처음 알려진 날"과 그때 공개된 금액을 종목·회계연도별로
db/financial_indicators.sqlite3(earnings_disclosures)에 쌓는다. 여러 리서치 공용.

1단계(목록): 전종목 공시 목록(OpenDART list.json)에서 실적 공시만 골라 적재.
2단계(원문): 지정 종목만 DART 뷰어 HTML을 받아 금액·회계기간 파싱.

Usage (from etl/):
    uv run python scripts/build_earnings_disclosures.py --from 20240101 --to 20240930
    uv run python scripts/build_earnings_disclosures.py --no-list --tickers-file tickers.txt
"""
from __future__ import annotations

import argparse
import calendar
import io
import re
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401,E402  (cp949 가드 + sys.path: etl/·scripts/·src/)

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from build_financial_indicators import DEFAULT_DB_PATH, parse_report_period  # noqa: E402
from new_etf_insight.dart_client import fetch_all_filings, get_api_key  # noqa: E402
from new_etf_insight.dart_pdf import fetch_dart_main_html  # noqa: E402
from wl_sqlite import connect_rw  # noqa: E402

VIEWER_URL = "https://dart.fss.or.kr/report/viewer.do"
MAX_PAGES = 1000  # 전종목 3개월창은 기본 50으로 모자람
LISTED_CORP_CLS = ("Y", "K")  # 유가증권 / 코스닥

_SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings_disclosures (
    rcept_no      TEXT PRIMARY KEY,
    rcept_dt      TEXT NOT NULL,
    corp_code     TEXT NOT NULL,
    stock_code    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    report_nm     TEXT NOT NULL,
    is_correction INTEGER NOT NULL,
    fs_basis      TEXT,
    fiscal_year   TEXT,
    period        TEXT,
    revenue       REAL,
    op_income     REAL,
    pretax_income REAL,
    net_income    REAL,
    parse_status  TEXT NOT NULL,
    parse_note    TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_earn_stock ON earnings_disclosures(stock_code, fiscal_year, rcept_dt);
"""

_UPSERT_LIST_SQL = """
INSERT INTO earnings_disclosures
    (rcept_no, rcept_dt, corp_code, stock_code, kind, report_nm, is_correction,
     fs_basis, fiscal_year, period, parse_status, parse_note, updated_at)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(rcept_no) DO UPDATE SET
    rcept_dt=excluded.rcept_dt, corp_code=excluded.corp_code,
    stock_code=excluded.stock_code, kind=excluded.kind,
    report_nm=excluded.report_nm, is_correction=excluded.is_correction,
    updated_at=excluded.updated_at
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(con) -> None:
    con.executescript(_SCHEMA)


def classify(report_nm: str) -> str | None:
    """공시 제목 → prelim / pnl_change / annual_report. 실적 무관·자회사는 None."""
    nm = (report_nm or "").strip()
    if "자회사" in nm:
        return None
    if "영업(잠정)실적" in nm:
        return "prelim"
    if "매출액또는손익구조" in nm:
        return "pnl_change"
    parsed = parse_report_period(nm)
    if parsed and parsed[1] == "11011":
        return "annual_report"
    return None


# ── 1단계: 목록 ───────────────────────────────────────────────────────────────


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def split_windows(begin: str, end: str) -> list[tuple[str, str]]:
    """[begin, end]를 3개월 이하 창들로 쪼갠다. 회사 미지정 list.json은 3개월 제한."""
    b = datetime.strptime(begin, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    if b > e:
        raise ValueError(f"begin > end: {begin} {end}")
    out: list[tuple[str, str]] = []
    cur = b
    while cur <= e:
        wend = min(_add_months(cur, 3) - timedelta(days=1), e)
        out.append((cur.strftime("%Y%m%d"), wend.strftime("%Y%m%d")))
        cur = wend + timedelta(days=1)
    return out


def upsert_list_rows(con, filings: list[dict]) -> int:
    """실적 대상 행만 업서트. 파싱済 필드는 건드리지 않는다(재실행 멱등)."""
    now = _now()
    n = 0
    for f in filings:
        nm = str(f.get("report_nm") or "").strip()
        kind = classify(nm)
        if kind is None:
            continue
        if f.get("corp_cls") not in LISTED_CORP_CLS:
            continue
        stock = str(f.get("stock_code") or "").strip()
        if not stock:
            continue
        is_corr = 1 if "[기재정정]" in nm else 0
        if kind == "annual_report":
            parsed = parse_report_period(nm)
            assert parsed is not None  # classify 통과 = 파싱 성공
            fiscal_year, period, status = parsed[0], "FY", "skip"
        else:
            fiscal_year, period, status = None, None, "pending"
        fs_basis = "CFS" if (kind == "prelim" and "연결" in nm) else None
        con.execute(
            _UPSERT_LIST_SQL,
            (f.get("rcept_no"), f.get("rcept_dt"), f.get("corp_code"), stock,
             kind, nm, is_corr, fs_basis, fiscal_year, period, status, None, now),
        )
        n += 1
    return n


def load_list(con, key: str, begin: str, end: str) -> dict:
    """창 × (I 거래소공시 / A 정기공시) 목록 적재. 부분 적재면 RuntimeError."""
    windows = split_windows(begin, end)
    received = loaded = 0
    for wi, (ws, we) in enumerate(windows):
        for ty in ("I", "A"):
            filings, last = fetch_all_filings(
                key, ws, we, page_count=100, max_pages=MAX_PAGES, pblntf_ty=ty)
            total = int(last.get("total_count") or 0)
            if len(filings) < total:
                raise RuntimeError(
                    f"부분 적재: {ws}~{we} {ty} 수신 {len(filings)} < total_count {total}")
            n = upsert_list_rows(con, filings)
            received += len(filings)
            loaded += n
            print(f"[목록 {ws}~{we} {ty}] 수신 {len(filings)} 적재 {n}")
        con.commit()
        if wi < len(windows) - 1:
            time.sleep(0.1)
    con.commit()
    return {"windows": len(windows), "filings": received, "loaded": loaded}


# ── 2단계: 원문 ───────────────────────────────────────────────────────────────

_VIEWDOC_RE = re.compile(
    r'viewDoc\("(\d+)", "(\d+)", "([^"]*)", "([^"]*)", "([^"]*)", "([^"]*)"')


def fetch_document_html(rcept_no: str) -> str:
    """DART 뷰어 문서 HTML. main.do의 viewDoc 파라미터로 viewer.do 조회."""
    main_html = fetch_dart_main_html(rcept_no)
    mch = _VIEWDOC_RE.search(main_html)
    if not mch:
        raise ValueError(f"viewDoc 파라미터 없음: {rcept_no}")
    rcp_no, dcm_no, ele_id, offset, length, dtd = mch.groups()
    resp = requests.get(
        VIEWER_URL,
        params={"rcpNo": rcp_no, "dcmNo": dcm_no, "eleId": ele_id,
                "offset": offset, "length": length, "dtd": dtd},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()
    resp.encoding = "cp949"
    return resp.text


# ── 순수 파서 ─────────────────────────────────────────────────────────────────

_UNIT_WORDS = (("조원", 1e12), ("억원", 1e8), ("백만원", 1e6), ("천원", 1e3), ("원", 1.0))

_PRELIM_Q_RE = re.compile(r"\('(\d{2})\.(\d)Q\)")
_PRELIM_Q_REV_RE = re.compile(r"\((\d)Q(\d{2})\)")
_PRELIM_RANGE_RE = re.compile(
    r"(\d{4})\.(\d{1,2})\.(\d{1,2})\s*~\s*(\d{4})\.(\d{1,2})\.(\d{1,2})")
PRELIM_LABELS = ("매출액", "영업이익", "법인세비용차감전계속사업이익", "당기순이익")
_PNL_LABELS = (("매출액", "revenue"), ("영업이익", "op_income"),
               ("법인세비용차감전계속사업이익", "pretax_income"),
               ("당기순이익", "net_income"))


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v != v:  # NaN
        return ""
    s = str(v).replace("\xa0", " ").strip()
    return "" if s.lower() == "nan" else s


def _num(s) -> float | None:
    """표 셀 → 숫자. △/▲·괄호는 음수, -·빈값·nan·비숫자는 None."""
    if s is None:
        return None
    t = str(s).strip().replace(",", "").replace(" ", "")
    if t in ("", "-", "nan", "NaN", "None"):
        return None
    neg = False
    if t[:1] in ("△", "▲"):
        neg = True
        t = t[1:]
    elif t.startswith("(") and t.endswith(")"):
        neg = True
        t = t[1:-1]
    if t in ("", "-"):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _unit(text: str) -> float:
    """'단위' 뒤 첫 단위어 → 배수. 긴 단어 우선. 못 찾으면 ValueError."""
    i = text.find("단위")
    if i < 0:
        raise ValueError(f"단위 표기 없음: {text[:60]!r}")
    tail = text[i + 2:]
    best_pos: int | None = None
    best_mult: float | None = None
    for word, mult in _UNIT_WORDS:
        p = tail.find(word)
        if p < 0:
            continue
        if best_pos is None or p < best_pos:
            best_pos, best_mult = p, mult
    if best_mult is None:
        raise ValueError(f"단위어 판정 불가: {tail[:60]!r}")
    return best_mult


def _all_tables_rows(html: str) -> list[list[list[str]]]:
    tables = pd.read_html(io.StringIO(html), header=None)
    if not tables:
        raise ValueError("표 없음")
    return [[[_cell(v) for v in row] for row in t.values.tolist()] for t in tables]


def _table_unit(rows: list[list[str]]) -> float:
    for cells in rows:
        text = " ".join(cells)
        if "단위" in text:
            return _unit(text)
    raise ValueError("단위 표기 없음")


def prelim_period_from_end_date(year: int, month: int, fye_month: int) -> tuple[str, str]:
    """코스닥형 기간(끝 날짜 y.m) → (회계연도, Qn). 결산월 기준 분기 역산."""
    n = ((month - fye_month - 1) % 12) // 3 + 1
    return (str(year) if month <= fye_month else str(year + 1)), f"Q{n}"


def pnl_fiscal_year(rcept_dt: str, fye_month: int) -> str:
    """공시일 직전에 끝난 회계연도. fye 말일(당해) >= 공시일이면 1년 전."""
    r = date(int(rcept_dt[0:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8]))
    fye_end = date(r.year, fye_month, calendar.monthrange(r.year, fye_month)[1])
    if fye_end >= r:
        fye_end = date(r.year - 1, fye_month, calendar.monthrange(r.year - 1, fye_month)[1])
    return str(fye_end.year)


def _parse_prelim(rows: list[list[str]], fye_month: int) -> dict:
    # 기간: ('24.4Q) → (4Q23) → 날짜구간 순. 첫 매치 열 = 당기실적 열.
    period_col: int | None = None
    fiscal_year: str | None = None
    period: str | None = None
    for cells in rows:
        for i, c in enumerate(cells):
            mch = _PRELIM_Q_RE.search(c)
            if mch:
                fiscal_year, period, period_col = "20" + mch.group(1), "Q" + mch.group(2), i
                break
        if period_col is not None:
            break
    if period_col is None:
        for cells in rows:
            for i, c in enumerate(cells):
                mch = _PRELIM_Q_REV_RE.search(c)
                if mch:
                    fiscal_year, period, period_col = "20" + mch.group(2), "Q" + mch.group(1), i
                    break
            if period_col is not None:
                break
    if period_col is None:
        for cells in rows:
            for i, c in enumerate(cells):
                mch = _PRELIM_RANGE_RE.search(c)
                if mch:
                    fiscal_year, period = prelim_period_from_end_date(
                        int(mch.group(4)), int(mch.group(5)), fye_month)
                    period_col = i
                    break
            if period_col is not None:
                break
    if period_col is None or fiscal_year is None or period is None:
        raise ValueError("잠정실적 기간 표기 없음")
    unit = _table_unit(rows)
    # 금액: 라벨+누계실적 행의 당기실적 열. 병합셀 미전개(rowspan NaN) 대비 라벨 추적.
    vals: dict[str, dict[str, float | None]] = {
        k: {"cum": None, "cur": None} for k in PRELIM_LABELS}
    cur: str | None = None
    for cells in rows:
        c0 = cells[0] if cells else ""
        c1 = cells[1] if len(cells) > 1 else ""
        if c0 in PRELIM_LABELS:
            label, div, vidx = c0, c1, period_col
        elif c0 in ("누계실적", "당해실적") and cur is not None:
            label, div, vidx = cur, c0, period_col - 1
        else:
            if c0:  # 다른 라벨 행(지배기업 소유주지분 순이익 등) → 추적 해제
                cur = None
            continue
        cur = label
        v = _num(cells[vidx]) if 0 <= vidx < len(cells) else None
        if div == "누계실적":
            vals[label]["cum"] = v
        elif div == "당해실적":
            vals[label]["cur"] = v
    out: dict[str, float | None] = {}
    note: str | None = None
    for key, label in (("revenue", "매출액"), ("op_income", "영업이익"),
                       ("pretax_income", "법인세비용차감전계속사업이익"),
                       ("net_income", "당기순이익")):
        v = vals[label]["cum"]
        if v is None and period == "Q1":
            v = vals[label]["cur"]
            if v is not None:
                note = "Q1 누계 없음→당해 사용"
        out[key] = v * unit if v is not None else None
    text = " ".join(c for row in rows for c in row)
    if "연결실적" in text:
        fs_basis = "CFS"
    elif "실적내용" in text and "연결" not in text:
        fs_basis = "OFS"
    else:
        fs_basis = None
    return {"fs_basis": fs_basis, "fiscal_year": fiscal_year, "period": period,
            **out, "note": note}


def _parse_pnl(rows: list[list[str]], rcept_dt: str, fye_month: int) -> dict:
    val_col: int | None = None
    for cells in rows:
        for i, c in enumerate(cells):
            if c == "당해사업연도":
                val_col = i
                break
        if val_col is not None:
            break
    if val_col is None:
        raise ValueError("당해사업연도 열 없음")
    unit = _table_unit(rows)
    out: dict[str, float | None] = {key: None for _, key in _PNL_LABELS}
    for cells in rows:
        c0 = cells[0] if cells else ""
        cleaned = c0.strip().lstrip("-").strip()
        for word, key in _PNL_LABELS:
            if cleaned.startswith(word):
                v = _num(cells[val_col]) if val_col < len(cells) else None
                out[key] = v * unit if v is not None else None
                break
    fs_basis: str | None = None
    for cells in rows:
        for j, c in enumerate(cells):
            if "재무제표의 종류" in c:
                tail = " ".join(cells[j + 1:])
                if "연결" in tail:
                    fs_basis = "CFS"
                elif "별도" in tail or "개별" in tail:
                    fs_basis = "OFS"
                break
        if fs_basis is not None:
            break
    return {"fs_basis": fs_basis, "fiscal_year": pnl_fiscal_year(rcept_dt, fye_month),
            "period": "FY", **out, "note": None}


def parse_document(html: str, kind: str, rcept_dt: str, fye_month: int) -> dict:
    """공시 원문 HTML → 금액·회계기간. kind는 prelim / pnl_change."""
    if kind not in ("prelim", "pnl_change"):
        raise ValueError(f"kind 오류: {kind}")
    last_exc: Exception | None = None
    for rows in _all_tables_rows(html):
        try:
            if kind == "prelim":
                return _parse_prelim(rows, fye_month)
            return _parse_pnl(rows, rcept_dt, fye_month)
        except Exception as e:
            last_exc = e
    assert last_exc is not None
    raise last_exc


def load_fye_months(con) -> dict[str, int]:
    """종목별 결산월. indicators(11011) stlm_dt 월의 최빈값. 없으면 빈 dict."""
    out: dict[str, int] = {}
    try:
        cur = con.execute(
            "SELECT stock_code, CAST(substr(stlm_dt,6,2) AS INTEGER) AS m, COUNT(*) AS c "
            "FROM indicators WHERE reprt_code='11011' AND stlm_dt IS NOT NULL "
            "AND stock_code IS NOT NULL GROUP BY stock_code, m "
            "ORDER BY stock_code, c DESC, m")
    except sqlite3.OperationalError:
        return {}
    for stock, m, _c in cur:
        if stock not in out and m is not None and 1 <= m <= 12:
            out[stock] = m
    return out


def parse_pending(con, stock_codes, force: bool = False) -> dict:
    """pending 원문 파싱. 실패 건은 failed로 남기고 계속."""
    codes = list(stock_codes or [])
    if not codes:
        return {"targets": 0, "ok": 0, "failed": 0, "fetch_failed": 0}
    fye = load_fye_months(con)
    qmarks = ",".join("?" for _ in codes)
    sql = (f"SELECT rcept_no, rcept_dt, stock_code, kind, is_correction "
           f"FROM earnings_disclosures "
           f"WHERE stock_code IN ({qmarks}) AND kind IN ('prelim','pnl_change')")
    if not force:
        sql += " AND parse_status='pending'"
    sql += " ORDER BY rcept_dt, rcept_no"
    rows = con.execute(sql, codes).fetchall()
    ok = fail = fetch_fail = 0
    for i, (rcept_no, rcept_dt, stock, kind, is_corr) in enumerate(rows, 1):
        try:
            try:
                html = fetch_document_html(rcept_no)
            finally:
                time.sleep(0.5)
        except Exception as e:
            con.execute(
                "UPDATE earnings_disclosures SET parse_status='pending', parse_note=?, "
                "updated_at=? WHERE rcept_no=?",
                (("fetch: " + str(e))[:200], _now(), rcept_no))
            fetch_fail += 1
            if i % 50 == 0:
                con.commit()
                print(f"[원문] {i}/{len(rows)} ok={ok} failed={fail} fetch_failed={fetch_fail}")
            continue
        try:
            res = parse_document(html, kind, rcept_dt, fye.get(stock, 12))
            if res["revenue"] is None and res["op_income"] is None:
                raise ValueError("매출액·영업이익 모두 없음")
            con.execute(
                "UPDATE earnings_disclosures SET fs_basis=?, fiscal_year=?, period=?, "
                "revenue=?, op_income=?, pretax_income=?, net_income=?, "
                "parse_status='ok', parse_note=?, updated_at=? WHERE rcept_no=?",
                (res["fs_basis"], res["fiscal_year"], res["period"], res["revenue"],
                 res["op_income"], res["pretax_income"], res["net_income"],
                 res["note"], _now(), rcept_no))
            ok += 1
        except Exception as e:
            if is_corr:
                con.execute(
                    "UPDATE earnings_disclosures SET parse_status='skip', parse_note=?, "
                    "updated_at=? WHERE rcept_no=?",
                    (("정정공시: 정정사항표만 " + str(e))[:200], _now(), rcept_no))
            else:
                con.execute(
                    "UPDATE earnings_disclosures SET parse_status='failed', parse_note=?, "
                    "updated_at=? WHERE rcept_no=?",
                    (str(e)[:200], _now(), rcept_no))
                fail += 1
        if i % 50 == 0:
            con.commit()
            print(f"[원문] {i}/{len(rows)} ok={ok} failed={fail} fetch_failed={fetch_fail}")
    con.commit()
    print(f"[원문] 완료 대상={len(rows)} ok={ok} failed={fail} fetch_failed={fetch_fail}")
    return {"targets": len(rows), "ok": ok, "failed": fail, "fetch_failed": fetch_fail}


# ── CLI ───────────────────────────────────────────────────────────────────────


def _read_tickers(path: Path) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        t = line.strip()
        if t and not t.startswith("#"):
            out.append(t)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="DART 실적 공시(잠정·30%변동·사업보고서) 적재")
    p.add_argument("--from", dest="from_", default="20180101", help="시작일 YYYYMMDD")
    p.add_argument("--to", default=None, help="종료일 YYYYMMDD (기본 오늘 KST)")
    p.add_argument("--no-list", action="store_true", help="목록 단계 생략")
    p.add_argument("--tickers-file", type=Path, default=None, help="원문 파싱 종목 파일")
    p.add_argument("--force-parse", action="store_true", help="파싱 상태 무관 재파싱")
    p.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = p.parse_args()

    to = args.to or datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
    key = None if args.no_list else get_api_key()
    args.db.parent.mkdir(parents=True, exist_ok=True)
    with connect_rw(args.db) as con:
        ensure_schema(con)
        if not args.no_list:
            assert key is not None
            load_list(con, key, args.from_, to)
        if args.tickers_file:
            parse_pending(con, _read_tickers(args.tickers_file), force=args.force_parse)
        for kind, status, c in con.execute(
                "SELECT kind, parse_status, COUNT(*) FROM earnings_disclosures "
                "GROUP BY kind, parse_status ORDER BY kind, parse_status"):
            print(f"[집계] {kind} {status}: {c}")


if __name__ == "__main__":
    main()
