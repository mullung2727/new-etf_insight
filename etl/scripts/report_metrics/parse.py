"""증권사 리포트 PDF → 사실(facts) / 추정표(estimates) 파싱.

리포트 1페이지 헤더는 증권사마다 배치가 다르지만 표기 어휘는 공통이다
(목표주가/현재주가/투자의견/상승여력/직전). 그래서 좌표가 아니라 **줄 단위 어휘**로 읽는다.
pypdf 추출 텍스트는 줄바꿈이 원본 표와 다르므로 표 좌표에 기대지 않는다.

한 번에 PDF 1개만 본다. 다른 리포트와의 비교(직전 목표가)는 DB 조회로 한다(PLAN §2.2).
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .models import (
    PARSER_VERSION,
    STATUS_IMAGE_PDF,
    STATUS_NO_TARGET,
    STATUS_OK,
    STATUS_PARSE_ERROR,
    ReportFacts,
    YearEstimate,
)

# 이 길이 미만이면 이미지 PDF로 본다. 실측: 미래에셋 리포트는 전 페이지 합쳐도 ~140자(PLAN §0).
MIN_TEXT_CHARS = 200

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 'TP 405,000원'(교보), '목표주 가(12M)'(iM) 처럼 글자 사이 공백·약어가 섞인다.
_TARGET_LINE = re.compile(r"목\s*표\s*주\s*가|목\s*표\s*가|(?<![A-Za-z])TP(?![A-Za-z])")
_PREV_TOKEN = re.compile(r"직\s*전")
_PRICE_LINE = re.compile(r"현\s*재\s*주\s*가|현재\s*가")
# 현재주가 줄이 없는 양식: '종가(2026.08.14) 48,300원'(iM), '주가(7/1): 24,800원'(키움)
_PRICE_FALLBACK_LINE = re.compile(r"^\s*(?:종\s*가|주\s*가)\s*\(")
_DATE_FULL = re.compile(r"\(\s*(20\d\d|\d\d)\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*\)")
# "(8 월 14 일)", "(8/14)", "(08/07)"
_DATE_MD = re.compile(r"\(\s*(\d{1,2})\s*(?:월|/)\s*(\d{1,2})\s*(?:일)?\s*\)")
_WON = re.compile(r"(\d[\d,]*)\s*원")
_UPSIDE = re.compile(r"(?:상승\s*여력|Upside)\D{0,10}(-?\d+(?:\.\d+)?)\s*%")
_OPINION = re.compile(
    r"(?:투자\s*의견|투자\s*판단|Rating)\s*[:：]?\s*"
    r"(NOT\s*RATED|매수|중립|보유|비중\s*확대|비중\s*축소|매도|Outperform|Underperform|"
    r"BUY|Buy|HOLD|Hold|SELL|Sell|TRADING\s*BUY|Trading\s*Buy|Marketperform)"
)

# --- 추정표 ---------------------------------------------------------------
# '2026F', '2025A', '2024', '2026(E)'. 날짜(2026.08.18)·'2026년' 은 제외.
_YEAR_TOKEN = re.compile(r"(?<![\d.])(20\d\d)\(?([AEFP])?\)?(?![\d.년])")
_YEAR_ROW = re.compile(r"^\s*(20\d\d)\(?([AEFP])?\)?\s+(.+)$")
# 라벨 뒤 괄호는 단위('(십억원)')만 라벨로 본다. '(3.1)' 같은 괄호 음수는 값이다.
_METRIC_ROW = re.compile(r"^\s*(?P<label>[^\d\-(]+?(?:\((?![\d.,\s-]+\))[^)]*\))?)\s+(?P<rest>[-\d(].*)$")
_UNIT = re.compile(r"(조원|십억원|억원|백만원|천원)")
_UNIT_TO_EOK = {"조원": 10000.0, "십억원": 10.0, "억원": 1.0, "백만원": 0.01, "천원": 0.00001}
_PLACEHOLDERS = {"-", "–", "n/a", "na", "nm", "적지", "적전", "흑전", "흑지", "적자", "흑자", "적확", "적축"}
_LABELS = {
    "매출액": "revenue", "매출": "revenue", "영업수익": "revenue", "순영업수익": "revenue",
    "REVENUE": "revenue", "SALES": "revenue",
    "영업이익": "operating_profit", "OP": "operating_profit",
    "당기순이익": "net_profit", "순이익": "net_profit", "NETPROFIT": "net_profit",
    "EPS": "eps", "PER": "per", "ROE": "roe", "PBR": "pbr",
    "DY": "div_yield", "배당수익률": "div_yield",
}
_AMOUNT_FIELDS = {"revenue", "operating_profit", "net_profit", "net_profit_ctrl"}
_METRIC_ROW_SCAN = 30   # 연도 헤더 아래로 이 줄 수까지만 행을 찾는다
_YEAR_ROW_SCAN = 12


def _to_number(token: str) -> float:
    return float(token.replace(",", ""))


def _numbers(line: str) -> list[float]:
    return [_to_number(m.group()) for m in _NUM.finditer(line)]


def parse_path(pdf_path: str | Path) -> dict[str, str]:
    """파일 경로 → stock_code/stock_name/broker/report_date/pdf_key (PLAN §2.3).

    디렉터리 `{종목명}_{종목코드}` 는 종목명에 '_' 가 있을 수 있어 rsplit,
    파일 stem `{날짜}_{증권사}_{key}` 는 key 에 '_' 가 많아 maxsplit=2 로 자른다.
    """
    path = Path(pdf_path)
    stock_name, _, stock_code = path.parent.name.rpartition("_")
    parts = path.stem.split("_", 2)
    if len(parts) != 3:
        raise ValueError(f"예상 밖 리포트 파일명: {path.name}")
    report_date, broker, pdf_key = parts
    return {
        "stock_code": stock_code,
        "stock_name": stock_name or None,
        "broker": broker,
        "report_date": report_date,
        "pdf_key": pdf_key,
    }


def _safe_date(year: int, month: int, day: int) -> Optional[str]:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _resolve_price_date(month: int, day: int, report_date: str) -> Optional[str]:
    """리포트에 연도 없이 적힌 월/일 → YYYY-MM-DD. 발간일보다 미래면 전년으로 본다."""
    try:
        year = int(report_date[:4])
    except (ValueError, IndexError):
        return None
    candidate = _safe_date(year, month, day)
    if candidate is not None and candidate > report_date:
        candidate = _safe_date(year - 1, month, day)
    return candidate


def _paren_date(line: str, report_date: str) -> tuple[Optional[re.Match], Optional[str]]:
    full = _DATE_FULL.search(line)
    if full:
        year = int(full.group(1))
        year = year + 2000 if year < 100 else year
        return full, _safe_date(year, int(full.group(2)), int(full.group(3)))
    md = _DATE_MD.search(line)
    if md:
        return md, _resolve_price_date(int(md.group(1)), int(md.group(2)), report_date)
    return None, None


# '목표주가 163만원'(iM 본문) — 만원 단위
_MAN_WON = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*만\s*원")
# 대신증권: '현재주가' / '(26.06.29)' / '41,400' 이 줄로 나뉜다. 이어 붙일 '값만 있는 줄'.
_PURE_CELL = re.compile(r"^\s*(?:\(\s*[\d./\s월일-]+\)|\d[\d,]*\s*원?)\s*$")


def _first_price(line: str) -> Optional[int]:
    """'4,520 원'/'163만원' 중 먼저 나온 것, 없으면 100 이상 첫 숫자.
    '(12M)' 같은 작은 수를 가격으로 잡지 않는다."""
    hits = []
    won = _WON.search(line)
    if won:
        hits.append((won.start(), int(_to_number(won.group(1)))))
    man = _MAN_WON.search(line)
    if man:
        hits.append((man.start(), int(_to_number(man.group(1)) * 10000)))
    if hits:
        return min(hits)[1]
    for m in _NUM.finditer(line):
        # '2026F PER', '12M', '6배' 처럼 뒤에 글자가 붙은 수는 가격이 아니다(대신증권 덴티움 사례)
        if re.match(r"[A-Za-z년배%만]", line[m.end(): m.end() + 1]):
            continue
        value = _to_number(m.group())
        if value >= 100:
            return int(value)
    return None


def parse_header(text: str, report_date: str) -> dict[str, Any]:
    """1페이지 텍스트 → 목표주가/현재주가/투자의견/인쇄 상승여력/인쇄 직전목표가."""
    out: dict[str, Any] = {
        "opinion": None,
        "target_price": None,
        "price_at_report": None,
        "price_at_report_date": None,
        "upside_printed": None,
        "prev_target_printed": None,
    }
    lines = text.splitlines()

    opinion = _OPINION.search(text)
    if opinion:
        out["opinion"] = re.sub(r"\s+", " ", opinion.group(1)).strip()
    elif re.search(r"NOT\s*RATED", text, re.IGNORECASE):
        out["opinion"] = "NOT RATED"

    has_prev_word = bool(_PREV_TOKEN.search(text))
    for line in lines:
        if not _TARGET_LINE.search(line):
            continue
        if _PREV_TOKEN.search(line):
            # "직전 목표주가 8,000원" — 직전값 줄. 목표가로 쓰면 안 된다.
            if out["prev_target_printed"] is None:
                out["prev_target_printed"] = _first_price(line)
            continue
        if out["target_price"] is None:
            out["target_price"] = _first_price(line)
        # "목표주가 7,000 7,000 -" 처럼 현재/직전이 한 줄에 오는 표 형식(유진투자 등)
        big = [int(v) for v in _numbers(line) if v >= 100]
        if has_prev_word and len(big) >= 2 and out["prev_target_printed"] is None:
            out["prev_target_printed"] = big[1]

    price_idx = [k for k, l in enumerate(lines) if _PRICE_LINE.search(l)] or [
        k for k, l in enumerate(lines) if _PRICE_FALLBACK_LINE.search(l)
    ]
    for k in price_idx:
        line = lines[k]
        match, _ = _paren_date(line, report_date)
        if _first_price(line if match is None else line[: match.start()] + line[match.end():]) is None:
            for nxt in lines[k + 1: k + 3]:     # 값이 다음 줄로 밀린 양식(대신증권)
                if not _PURE_CELL.match(nxt):
                    break
                line += " " + nxt.strip()
        match, iso = _paren_date(line, report_date)
        cleaned = line if match is None else line[: match.start()] + " " + line[match.end():]
        if iso and out["price_at_report_date"] is None:
            out["price_at_report_date"] = iso
        price = _first_price(cleaned)
        if price is not None and out["price_at_report"] is None:
            out["price_at_report"] = price
        if out["price_at_report"] is not None and out["price_at_report_date"] is not None:
            break

    upside = _UPSIDE.search(text)
    if upside:
        out["upside_printed"] = round(float(upside.group(1)) / 100.0, 6)
    return out


def classify_status(text: str, header: dict[str, Any]) -> str:
    """실패를 값으로 남긴다 — 조용한 스킵 금지(PLAN §0)."""
    if len(text.strip()) < MIN_TEXT_CHARS:
        return STATUS_IMAGE_PDF
    if header.get("target_price") is None:
        return STATUS_NO_TARGET
    return STATUS_OK


# --- 추정표 ---------------------------------------------------------------

def _unit_in(s: str) -> Optional[str]:
    m = _UNIT.search(re.sub(r"\s+", "", s))
    return m.group(1) if m else None


def _field_for(label: str) -> Optional[str]:
    raw = re.sub(r"\s+", "", label)
    if "순이익" in raw and "지배" in raw and "비지배" not in raw:
        return "net_profit_ctrl"
    core = re.sub(r"\(.*?\)", "", raw).upper()
    return _LABELS.get(core)


def _cell(token: str) -> tuple[bool, Optional[float]]:
    """표 셀 1개 → (셀로 인정?, 값). '(3.1)'=-3.1, '-'/'적지' 등은 빈 셀."""
    t = token.strip().rstrip("%")
    if t.lower() in _PLACEHOLDERS:
        return True, None
    neg = re.fullmatch(r"\((\d[\d,]*(?:\.\d+)?)\)", t)
    if neg:
        return True, -_to_number(neg.group(1))
    if re.fullmatch(r"-?\d[\d,]*(?:\.\d+)?", t):
        return True, _to_number(t)
    return False, None


def _cells(rest: str) -> Optional[list[Optional[float]]]:
    values = []
    for token in rest.split():
        ok, value = _cell(token)
        if not ok:
            return None     # '▲' 같은 비수치 토큰이 섞이면 표 행이 아니다
        values.append(value)
    return values


def _header_years(line: str) -> Optional[list[tuple[int, str]]]:
    """연도가 2개 이상 연속으로 나열된 줄 = 지표행 방향 표의 헤더."""
    tokens = [(int(y), s or "") for y, s in _YEAR_TOKEN.findall(line)]
    if len(tokens) < 2:
        return None
    years = [y for y, _ in tokens]
    if any(b - a != 1 for a, b in zip(years, years[1:])):
        return None
    return tokens


def _is_forecast(year: int, suffix: str, report_year: Optional[int]) -> bool:
    if suffix in ("E", "F", "P"):
        return True
    if suffix == "A":
        return False
    return report_year is not None and year >= report_year


def _put(table: dict, year: int, forecast: bool, field: str, value: Optional[float]) -> None:
    row = table.setdefault(year, {"is_forecast": forecast})
    if value is not None and row.get(field) is None:
        row[field] = value      # 먼저 찾은 표가 우선, 뒤 표는 빈 칸만 채운다


def _scale(field: str, value: Optional[float], unit: Optional[str]) -> Optional[float]:
    if value is None or field not in _AMOUNT_FIELDS:
        return value
    if unit is None:
        return None             # 단위를 모르는 금액은 추정하지 않는다(PLAN §2.4)
    return round(value * _UNIT_TO_EOK[unit], 6)


def _table_unit(lines: list[str], i: int) -> Optional[str]:
    for j in (i, i + 1, i - 1, i - 2):
        if 0 <= j < len(lines):
            unit = _unit_in(lines[j])
            if unit:
                return unit
    return None


def _scan_metric_rows(lines, i, years, report_year, table) -> None:
    """'매출액 1,070.1 1,234.9 1,394.3' 처럼 지표가 행인 표(유진·유안타·키움 등)."""
    unit = _table_unit(lines, i)
    for line in lines[i + 1: i + 1 + _METRIC_ROW_SCAN]:
        if _header_years(line):
            break
        m = _METRIC_ROW.match(line)
        if not m:
            continue
        field = _field_for(m.group("label"))
        values = _cells(m.group("rest")) if field else None
        if not values or len(values) != len(years):
            continue
        row_unit = _unit_in(m.group("label")) or unit
        for (year, suffix), value in zip(years, values):
            _put(table, year, _is_forecast(year, suffix, report_year), field,
                 _scale(field, value, row_unit))


def _metric_header(line: str) -> Optional[list[Optional[str]]]:
    """'매출액 영업이익 지배순이익 PER ROE ...' 처럼 지표가 열인 표의 헤더(신한 등)."""
    fields = [_field_for(t) for t in line.split()]
    if sum(1 for f in fields if f) < 3:
        return None
    first = next(k for k, f in enumerate(fields) if f)
    return fields[first:]


def _scan_year_rows(lines, i, columns, report_year, table) -> None:
    unit = _table_unit(lines, i)
    col_units: list[Optional[str]] = [unit] * len(columns)
    for line in lines[i + 1: i + 1 + _YEAR_ROW_SCAN]:
        unit_tokens = re.findall(r"\(([^)]*)\)", line)
        if len(unit_tokens) == len(columns) and not _YEAR_ROW.match(line):
            col_units = [_unit_in(u) for u in unit_tokens]   # 열별 단위 줄
            continue
        m = _YEAR_ROW.match(line)
        if not m:
            continue
        values = _cells(m.group(3))
        if not values or len(values) != len(columns):
            continue
        year, suffix = int(m.group(1)), m.group(2) or ""
        for field, value, col_unit in zip(columns, values, col_units):
            if field:
                _put(table, year, _is_forecast(year, suffix, report_year), field,
                     _scale(field, value, col_unit))


def parse_estimates(text: str, report_year: Optional[int] = None) -> list[YearEstimate]:
    """추정표 → 연도별 추정치. 못 찾으면 빈 리스트.

    두 방향을 다 읽는다: 지표가 행(연도가 헤더) / 연도가 행(지표가 헤더).
    같은 연도가 여러 표에 나오면 먼저 나온 값이 이긴다(1페이지 요약표 우선).
    연도에 A/E/F 표기가 없으면 report_year 이상을 추정치로 본다.
    """
    lines = text.splitlines()
    table: dict[int, dict[str, Any]] = {}
    for i, line in enumerate(lines):
        years = _header_years(line)
        if years:
            _scan_metric_rows(lines, i, years, report_year, table)
            continue
        columns = _metric_header(line)
        if columns:
            _scan_year_rows(lines, i, columns, report_year, table)

    out = []
    for year in sorted(table):
        row = table[year]
        net = row.get("net_profit_ctrl")
        out.append(YearEstimate(
            fiscal_year=year,
            is_forecast=row["is_forecast"],
            revenue=row.get("revenue"),
            operating_profit=row.get("operating_profit"),
            net_profit=net if net is not None else row.get("net_profit"),
            eps=row.get("eps"),
            per=row.get("per"),
            roe=row.get("roe"),
            pbr=row.get("pbr"),
            div_yield=row.get("div_yield"),
        ))
    # 값이 하나도 없는 연도는 헤더만 걸린 것 — 버린다
    return [e for e in out if any(
        getattr(e, f) is not None
        for f in ("revenue", "operating_profit", "net_profit", "eps", "per", "roe", "pbr", "div_yield")
    )]


def read_pages(pdf_path: str | Path) -> list[str]:
    """PDF → 페이지별 텍스트. pypdf 는 손상 PDF 에서 다양한 예외를 던지므로 호출부가 감싼다."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def extract_text(pdf_path: str | Path, max_pages: Optional[int] = None) -> str:
    """PDF → 텍스트. 실패 시 빈 문자열(호출부가 parse_status 로 분류)."""
    try:
        pages = read_pages(pdf_path)
    except Exception:
        return ""
    return "\n".join(pages[:max_pages] if max_pages else pages)


def parse_report(pdf_path: str | Path) -> tuple[ReportFacts, list[YearEstimate]]:
    """한 PDF → (facts, estimates). 예외를 밖으로 내지 않고 parse_status 에 담는다."""
    meta = parse_path(pdf_path)
    facts = ReportFacts(
        pdf_key=meta["pdf_key"],
        pdf_path=str(pdf_path),
        stock_code=meta["stock_code"],
        stock_name=meta["stock_name"],
        broker=meta["broker"],
        report_date=meta["report_date"],
        parser_version=PARSER_VERSION,
    )
    try:
        pages = read_pages(pdf_path)
    except Exception as exc:  # 손상 PDF 1건이 전체 배치를 멈추게 하지 않는다
        facts.parse_status = STATUS_PARSE_ERROR
        facts.parse_error = f"{type(exc).__name__}: {exc}"[:500]
        return facts, []

    full_text = "\n".join(pages)
    header = parse_header(pages[0] if pages else "", facts.report_date)
    for key, value in header.items():
        setattr(facts, key, value)
    facts.parse_status = classify_status(full_text, header)
    if facts.parse_status == STATUS_IMAGE_PDF:
        return facts, []   # 이미지 PDF 는 지원 안 함(OCR 범위 밖) — 상태만 남긴다

    try:
        estimates = parse_estimates(full_text, report_year=int(facts.report_date[:4]))
    except Exception as exc:
        facts.parse_error = f"estimates: {type(exc).__name__}: {exc}"[:500]
        estimates = []
    return facts, estimates
