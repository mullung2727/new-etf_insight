"""섹터·시황 PDF → 종목 연결 판정 (규칙 v1, 설계 §8-6).

LLM 없이 정규식+종목사전 매칭만. 이미지 PDF(텍스트 없음)는 판정 생략(OCR 안 함).
report_api_facts/report_facts/report_estimates에는 절대 쓰지 않는다(설계 §4 분석 경계).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import catalog

# 머리글: 페이지 앞 3줄 중 매치. 예: 피에스케이 (319660), JYP Ent. (035900)
_HEADER_RE = re.compile(
    r"^(?P<name>.{1,30}?)\s*\(\s*A?(?P<code>\d{6})"
    r"(?:\.K[SQ])?\s*(?:KS|KQ|KOSPI|KOSDAQ)?\s*\)"
)
_CODE_FIND_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_KEYWORDS = ("투자의견", "목표주가")


def _is_word_char(ch: str) -> bool:
    """종목명 앞 글자 금지 판정용: 한글/영문/숫자면 True."""
    return ("가" <= ch <= "힣") or ("A" <= ch <= "Z") or ("a" <= ch <= "z") or ("0" <= ch <= "9")


def pages_text(pdf_path: Path) -> list[str]:
    """PDF → 페이지별 텍스트. 실패 시 [] (호출부가 수집 실패로 만들지 않음)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        return [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return []


def _mention_codes_in_text(
    text: str,
    code_to_name: dict[str, str],
    name_to_code: dict[str, str],
    sorted_names: list[str],
) -> set[str]:
    """한 페이지 본문에서 언급된 종목코드 집합. 이름은 가장 긴 것 우선+마스킹."""
    found: set[str] = set()
    for m in _CODE_FIND_RE.finditer(text):
        if m.group(0) in code_to_name:
            found.add(m.group(0))
    masked = bytearray(len(text))
    for nm in sorted_names:
        code = name_to_code[nm]
        nl = len(nm)
        start = 0
        while True:
            idx = text.find(nm, start)
            if idx < 0:
                break
            if (idx == 0 or not _is_word_char(text[idx - 1])) and not any(masked[idx:idx + nl]):
                found.add(code)
                for j in range(idx, idx + nl):
                    masked[j] = 1
            start = idx + 1
    return found


def judge(
    pages: list[str],
    code_to_name: dict[str, str],
    name_to_code: dict[str, str],
) -> list[dict]:
    """페이지 텍스트 → 종목 연결 판정. 순수 함수. 종목당 1건, primary > unknown > mention.

    각 dict: stock_code, relation_type, page_from, page_to (1-based).
    """
    n = len(pages)
    if n == 0 or not code_to_name:
        return []
    texts = [p or "" for p in pages]

    # 1. 머리글 수집: [(0-based 페이지, 코드)]
    headers: list[tuple[int, str]] = []
    for i, text in enumerate(texts):
        lines = [ln for ln in text.splitlines() if ln.strip()]
        seen_page: set[str] = set()
        for ln in lines[:3]:
            m = _HEADER_RE.match(ln.strip())
            if m and m.group("code") in code_to_name and m.group("code") not in seen_page:
                seen_page.add(m.group("code"))
                headers.append((i, m.group("code")))

    # 2. primary: 머리글 페이지에 투자의견/목표주가
    primary_first: dict[str, int] = {}  # code -> 첫 primary 페이지(1-based)
    for i, code in headers:
        if _KEYWORDS[0] in texts[i] or _KEYWORDS[1] in texts[i]:
            p = i + 1
            if code not in primary_first or p < primary_first[code]:
                primary_first[code] = p
    primary_range: dict[str, tuple[int, int]] = {}
    for code, p_from in primary_first.items():
        nxt = None
        for o_code, o_p in primary_first.items():
            if o_code != code and o_p > p_from and (nxt is None or o_p < nxt):
                nxt = o_p
        primary_range[code] = (p_from, (nxt - 1) if nxt is not None else n)

    # 3. unknown: 머리글은 있으나 키워드 없음. 같은 종목 primary 구간 안이면 흡수.
    header_pages: dict[str, list[int]] = {}
    for i, code in headers:
        header_pages.setdefault(code, []).append(i)
    unknown_range: dict[str, tuple[int, int]] = {}
    for code, idxs in header_pages.items():
        if code in primary_range:
            continue  # primary 우선 (구간 안 연속 머리글은 흡수, 별도 행 없음)
        unknown_range[code] = (min(idxs) + 1, max(idxs) + 1)

    # 4. mention: 코드 또는 종목명 등장. 요약표 코드만으로 primary 금지.
    excluded = set(primary_range) | set(unknown_range)
    sorted_names = sorted(
        (nm for nm in name_to_code if len(nm) >= 3), key=len, reverse=True
    )
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    for i, text in enumerate(texts):
        if not text:
            continue
        for code in _mention_codes_in_text(text, code_to_name, name_to_code, sorted_names):
            if code in excluded:
                continue
            if code not in first:
                first[code] = i + 1
            last[code] = i + 1

    out = [
        {"stock_code": c, "relation_type": "primary", "page_from": pf, "page_to": pt}
        for c, (pf, pt) in primary_range.items()
    ]
    out += [
        {"stock_code": c, "relation_type": "unknown", "page_from": pf, "page_to": pt}
        for c, (pf, pt) in unknown_range.items()
    ]
    out += [
        {"stock_code": c, "relation_type": "mention", "page_from": pf, "page_to": last[c]}
        for c, pf in first.items()
    ]
    out.sort(key=lambda d: (d["page_from"], d["stock_code"]))
    return out


def load_names() -> tuple[dict[str, str], dict[str, str]]:
    """DuckDB read_only로 (code_to_name, name_to_code). 실패 시 ({}, {})."""
    try:
        import duckdb

        try:
            from scripts.build_krx_ohlcv import DEFAULT_DB_PATH
            from scripts.stock_names import load_code_to_name, load_name_to_code
        except ImportError:
            from build_krx_ohlcv import DEFAULT_DB_PATH
            from stock_names import load_code_to_name, load_name_to_code
        with duckdb.connect(str(DEFAULT_DB_PATH), read_only=True) as con:
            return load_code_to_name(con), load_name_to_code(con)
    except Exception:
        return {}, {}


def link_document(
    con: sqlite3.Connection,
    document_id: int,
    pdf_path: Path,
    code_to_name: dict[str, str],
    name_to_code: dict[str, str],
    broker: str | None = None,
) -> int:
    """한 문서의 종목 연결을 판정·저장(method='rule_v1'). 연결 수 반환.

    사전이 비었거나 텍스트가 없으면(이미지 PDF) 0. 예외는 던지지 않고 [] 취급.
    broker(발행 증권사)가 상장사명과 같으면 그 종목의 '단순 언급'은 버린다 —
    매 페이지 머리말·꼬리말에 찍혀 모든 자사 리포트에 붙는다(키움증권·유진투자증권 실측).
    """
    self_code = name_to_code.get(broker) if broker else None
    if not code_to_name or not name_to_code:
        return 0
    try:
        pages = pages_text(pdf_path)
    except Exception:
        return 0
    if not pages or all(len(p or "") < 50 for p in pages):
        return 0
    # KRX 신형 영문혼합 코드(예: 0008Z0)는 카탈로그·종목 배치 전체가 숫자 6자리만 다루므로 제외.
    rows = [r for r in judge(pages, code_to_name, name_to_code)
            if r["stock_code"].isdigit()
            and not (r["stock_code"] == self_code and r["relation_type"] == "mention")]
    for r in rows:
        catalog.upsert_document_stock(
            con, document_id=document_id, stock_code=r["stock_code"],
            relation_type=r["relation_type"], method="rule_v1",
            page_from=r["page_from"], page_to=r["page_to"])
    return len(rows)
