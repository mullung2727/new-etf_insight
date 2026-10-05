"""제3자배정 본문 파싱 → out/third_parsed.csv (SPEC T8~T12).

입력: out/third_events.csv 의 acptno
  → cache/viewer_{acptno}.json docs 에서 원본 docno
    ([정정] 아닌 가장 이른 date, fetch_kind.orig_docno 재사용)
  → cache/body_{docno}.html 파싱.

실행: python research/rights_issue/parse_third.py [--selfcheck] [--scope all]
  [--events <csv> --out <csv>]

--scope all: 입력 out/paid_events.csv → 출력 out/paid_parsed.csv (SPEC P1~P7).
기본 동작·third_*.csv 는 그대로.
--events/--out: 해당 csv 의 acptno 파싱(scope all 규칙) → out 저장.
유형 컬럼 있으면 유상만.

규칙:
- 숫자(1/3/4/6/7/7-2): 태그 제거 텍스트 + 앵커 정규식.
  구서식(7. 할인율 직접)과 신서식(7. 기준주가 + 7-2) 둘 다 처리.
- 할인율 부호: 할인=음수, 할증=양수. "할인율 10%"/"10" → -10,
  "할증율 5%" → +5, "-10" → -10. 부호 미상(부호·단어 없음)은
  발행가/기준가 비교로 부호 결정. 없으면 (발행가/기준가-1)*100.
- 주식수·자금액 "-": 0 (기타주식이 대부분 "-"라 0이어야 dilution 계산됨).
  라벨 자체가 없으면 NaN. 발행가·기준가 "-": NaN.
- 투자자 표: 헤더에 '최대주주와의 관계'+'배정주식수' 둘 다 있는 <table>.
  합계 행 제외.
- 법인 표: 헤더에 '명칭'+'출자자수'(+'대표이사' 등) 있는 <table>.
- inv_type (T9): 관계→최대주주·특수관계인, 이름→투자조합·펀드,
  corp_names/법인 키워드→전략적 법인, 나머지 개인, 복수→혼합, 표 없음→미상.
- 못 찾으면 NaN/빈칸, 예외로 죽지 않음.
"""
import json
import re
import sys
from html import unescape
from pathlib import Path

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"
OUT = ROOT / "out"

sys.path.insert(0, str(ROOT))
import fetch_kind as FK

VAL = r"([+-]?[\d,]+(?:\.\d+)?|-)"
P1 = r"1\.\s*신주의\s*종류와\s*수"
P2 = r"2\.\s*1주당\s*액면가액"
P3 = r"3\.\s*증자전\s*발행주식총수"
P4 = r"4\.\s*자금조달의\s*목적"
P5 = r"5\.\s*증자방식"
P6 = r"6\.\s*신주\s*발행가액"
P7NEW = r"7\.\s*기준주가(?!\s*에\s*대한)"
P7OLD = r"7\.\s*기준주가에\s*대한\s*할인[율률]\s*또는\s*할증[율률]"
P71 = r"7-1\."
P72 = r"7-2\.\s*기준주가에\s*대한\s*할인[율률]\s*또는\s*할증[율률]"
P73 = r"7-3\."
P8 = r"8\.\s*제3자배정"

FUNDS = [
    ("시설자금", "fund_facility"),
    ("영업양수자금", "fund_acquire"),
    ("운영자금", "fund_operating"),
    ("채무상환자금", "fund_debt"),
    (r"타법인\s*증권\s*취득자금", "fund_securities"),
    ("기타자금", "fund_other"),
]

PURPOSE_NAMES = {
    "fund_facility": "시설",
    "fund_acquire": "영업양수",
    "fund_operating": "운영",
    "fund_debt": "채무상환",
    "fund_securities": "타법인증권",
    "fund_other": "기타",
}
FUND_COLS = [k for _, k in FUNDS]

REL_KEYS = ("최대주주", "특수관계", "대표이사", "임원")
FUND_KEYS = ("조합", "펀드", "사모", "PEF", "신기술", "투자")
CORP_KEYS = ("주식회사", "(주)", "㈜", "Co", "Ltd", "Inc")

COLS = ["acptno", "docno", "new_common", "new_other", "pre_common", "pre_other",
        "dilution", "issue_price", "base_price", "discount_pct",
        "fund_facility", "fund_acquire", "fund_operating", "fund_debt",
        "fund_securities", "fund_other",
        "fund_total", "purpose", "method",
        "n_investors", "investors", "corp_names", "inv_type", "mgmt_flag",
        "parse_ok"]


def norm(s):
    return re.sub(r"\s+", "", s or "")


def html_to_text(html):
    s = re.sub(r"(?is)<script.*?</script>", " ", html or "")
    s = re.sub(r"(?is)<style.*?</style>", " ", s)
    s = re.sub(r"(?s)<[^>]*>", " ", s)
    return re.sub(r"\s+", " ", unescape(s).replace("\xa0", " ")).strip()


def clean_cell(s):
    s = re.sub(r"(?s)<[^>]*>", " ", s or "")
    s = unescape(s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def tok_int(tok, dash_zero=True):
    t = (tok or "").replace(",", "").strip()
    if t in ("", "-", "–", "—", "―", "－"):
        return 0 if dash_zero else None
    try:
        return int(t)
    except ValueError:
        try:
            return int(float(t))
        except ValueError:
            return None


def cell_int(cell):
    m = re.search(r"[+-]?[\d,]+", (cell or "").replace(" ", ""))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def seg(text, start, ends):
    m = re.search(start, text)
    if not m:
        return ""
    rest = text[m.end():]
    cut = len(rest)
    for e in ends:
        me = re.search(e, rest)
        if me:
            cut = min(cut, me.start())
    return rest[:cut]


def stock_pair(s):
    c = o = None
    m = re.search(r"보통주식\s*\(\s*주\s*\)\s*" + VAL, s)
    if m:
        c = tok_int(m.group(1))
    m = re.search(r"기타주식\s*\(\s*주\s*\)\s*" + VAL, s)
    if m:
        o = tok_int(m.group(1))
    return c, o


def price_in(s):
    m = re.search(r"보통주식\s*\(\s*원\s*\)\s*" + VAL, s)
    return tok_int(m.group(1), dash_zero=False) if m else None


def discount_in(s):
    """(값, 부호확정여부). 확정=명시 +/-·숫자 앞 할인/할증 단어."""
    m = re.search(r"(?:\(\s*%\s*\))?\s*(할인[율률]?|할증[율률]?)?\s*" + VAL, s)
    if not m:
        return None, False
    t = (m.group(2) or "").replace(",", "")
    if t in ("", "-"):
        return None, False
    try:
        v = float(t)
    except ValueError:
        return None, False
    w = m.group(1) or ""
    if w.startswith("할증") or (not w and "할증" in s and "할인" not in s):
        return abs(v), True
    if t[:1] in ("+", "-") or w:
        return -abs(v), True
    return -abs(v), False


def dilution_of(nc, no, pc, po):
    if nc is None and no is None:
        return None
    if pc is None and po is None:
        return None
    try:
        d = (pc or 0) + (po or 0)
        if not d:
            return None
        return ((nc or 0) + (no or 0)) / d
    except (TypeError, ZeroDivisionError):
        return None


def _fnum(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    return v


def fund_total_of(rec):
    vals = [_fnum(rec.get(k)) for k in FUND_COLS]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def purpose_of(rec):
    """P3: fund_total > 0 일 때 비중 50% 이상 항목명, 없으면 혼합."""
    t = fund_total_of(rec)
    if t is None or t == 0:
        return "현금없음"
    for k in FUND_COLS:
        v = _fnum(rec.get(k)) or 0
        if v / t >= 0.5:
            return PURPOSE_NAMES[k]
    return "혼합"


def method_of(s5):
    """P6: 5. 증자방식 구간 텍스트 → 제3자배정/주주배정/일반공모/미상.

    캐시 실측: 구간 셀은 "제3자배정증자" 같은 짧은 명칭 하나. 본문 뒤 정관
    근거에 "일반공모증자방식"이 항상 나오므로 반드시 구간 텍스트로만 판정.
    "주주배정후 실권주 일반공모"가 일반공모를 포함하므로 주주배정을 먼저 본다.
    """
    s = s5 or ""
    if "제3자" in s:
        return "제3자배정"
    if "주주배정" in s or "주주우선" in s:
        return "주주배정"
    if "일반공모" in s:
        return "일반공모"
    return "미상"


def numbers_from_text(text):
    s1 = seg(text, P1, [P2])
    s3 = seg(text, P3, [P4])
    s4 = seg(text, P4, [P5, P6])
    s6 = seg(text, P6, [r"7\."])
    s7 = seg(text, P7NEW, [P71, P72, P8])
    s72 = seg(text, P72, [P73, P8])
    s7o = seg(text, P7OLD, [P8])
    nc, no = stock_pair(s1)
    pc, po = stock_pair(s3)
    out = {"new_common": nc, "new_other": no, "pre_common": pc,
           "pre_other": po}
    for pat, key in FUNDS:
        m = re.search(pat + r"\s*\(\s*원\s*\)\s*" + VAL, s4)
        out[key] = tok_int(m.group(1)) if m else None
    issue = price_in(s6)
    base = price_in(s7)
    disc, fixed = discount_in(s72) if s72 else (None, False)
    if disc is None and s7o:
        disc, fixed = discount_in(s7o)
    if disc is not None and not fixed and issue and base:
        if issue > base:
            disc = abs(disc)
        elif issue < base:
            disc = -abs(disc)
        else:
            disc = 0
    if disc is None and issue and base:
        try:
            disc = (issue / base - 1) * 100
        except ZeroDivisionError:
            disc = None
    out["issue_price"] = issue
    out["base_price"] = base
    out["discount_pct"] = disc
    out["dilution"] = dilution_of(nc, no, pc, po)
    out["method"] = method_of(seg(text, P5, [P6]))
    out["fund_total"] = fund_total_of(out)
    out["purpose"] = purpose_of(out)
    return out


def html_tables(html):
    tabs = []
    for tm in re.finditer(r"(?is)<table\b.*?</table\s*>", html or ""):
        rows = []
        for rm in re.finditer(r"(?is)<tr\b.*?>(.*?)</tr\s*>", tm.group(0)):
            cells = [clean_cell(c) for c in
                     re.findall(r"(?is)<t[dh]\b.*?>(.*?)</t[dh]\s*>", rm.group(1))]
            if cells:
                rows.append(cells)
        if rows:
            tabs.append(rows)
    return tabs


def investors_from_html(html):
    invs = []
    for rows in html_tables(html):
        hi = None
        for i, r in enumerate(rows):
            j = norm("".join(r))
            if "최대주주와의관계" in j and "배정주식수" in j:
                hi = i
                break
        if hi is None:
            continue
        hn = [norm(c) for c in rows[hi]]
        ni = next((i for i, c in enumerate(hn) if "제3자배정대상자" in c), None)
        ri = next((i for i, c in enumerate(hn) if "최대주주와의관계" in c), None)
        si = next((i for i, c in enumerate(hn) if "선정경위" in c), None)
        sh = next((i for i, c in enumerate(hn) if "배정주식수" in c), None)
        if ni is None or sh is None:
            continue
        for r in rows[hi + 1:]:
            j = norm("".join(r))
            if "최대주주와의관계" in j and "배정주식수" in j:
                continue
            name = r[ni].strip() if ni < len(r) else ""
            nn = norm(name)
            if not nn or nn in ("-", "–", "계", "합계", "총계") or "합계" in nn:
                continue
            invs.append({
                "name": name,
                "relation": r[ri].strip() if ri is not None and ri < len(r) else "",
                "reason": r[si].strip() if si is not None and si < len(r) else "",
                "shares": cell_int(r[sh]) if sh < len(r) else None,
            })
    return invs


def corps_from_html(html):
    names = []
    for rows in html_tables(html):
        hi = None
        for i, r in enumerate(rows):
            j = norm("".join(r))
            if ("명칭" in j and "출자자수" in j
                    and ("대표이사" in j or "업무집행자" in j or "최대주주" in j)):
                hi = i
                break
        if hi is None:
            continue
        hn = [norm(c) for c in rows[hi]]
        ni = next((i for i, c in enumerate(hn) if "명칭" in c), None)
        if ni is None:
            continue
        rest = rows[hi + 1:]
        if not rest:
            continue
        full = max(len(r) for r in rest)
        for r in rest:
            if len(r) != full or ni >= len(r):
                continue
            nm = r[ni].strip()
            nn = norm(nm)
            if (not nn or nn in ("-", "–", "성명") or "지분" in nn
                    or "합계" in nn or "명칭" in nn or "출자자수" in nn):
                continue
            names.append(nm)
    seen, out = set(), []
    for n in names:
        k = norm(n)
        if k not in seen:
            seen.add(k)
            out.append(n)
    return out


def classify_one(name, relation, corp_set):
    if any(k in (relation or "") for k in REL_KEYS):
        return "최대주주·특수관계인"
    if any(k in (name or "") for k in FUND_KEYS):
        return "투자조합·펀드"
    if norm(name) in corp_set or any(k in (name or "") for k in CORP_KEYS):
        return "전략적 법인"
    return "개인"


def inv_type_of(investors, corps):
    if not investors:
        return "미상"
    corp_set = {norm(c) for c in corps}
    types = {classify_one(v["name"], v["relation"], corp_set) for v in investors}
    return next(iter(types)) if len(types) == 1 else "혼합"


def mgmt_from_text(text):
    return any(k in (text or "") for k in ("최대주주 변경", "최대주주변경", "경영권"))


def empty_record(ac, docno):
    return {"acptno": ac, "docno": docno, "new_common": None, "new_other": None,
            "pre_common": None, "pre_other": None, "dilution": None,
            "issue_price": None, "base_price": None, "discount_pct": None,
            "fund_facility": None, "fund_acquire": None, "fund_operating": None,
            "fund_debt": None, "fund_securities": None, "fund_other": None,
            "fund_total": None, "purpose": "현금없음", "method": "미상",
            "n_investors": 0, "investors": "", "corp_names": "", "inv_type": "미상",
            "mgmt_flag": False, "parse_ok": False}


def record_from_html(ac, docno, html, scope_all=False):
    rec = empty_record(ac, docno)
    rec["has_body"] = bool(html)
    if not html:
        return rec
    try:
        text = html_to_text(html)
        rec.update(numbers_from_text(text))
        invs = investors_from_html(html)
        corps = corps_from_html(html)
        rec["n_investors"] = len(invs)
        rec["investors"] = ";".join(f"{v['name']}|{v['relation']}" for v in invs)
        rec["corp_names"] = ";".join(corps)
        rec["inv_type"] = inv_type_of(invs, corps)
        rec["mgmt_flag"] = mgmt_from_text(text)
        if scope_all:
            rec["parse_ok"] = (rec["dilution"] is not None
                               and rec["fund_total"] is not None)
        else:
            rec["parse_ok"] = (bool(invs) and rec["dilution"] is not None
                               and rec["discount_pct"] is not None)
    except Exception:
        pass
    return rec


def is_scope_all(argv):
    i = argv.index("--scope") if "--scope" in argv else -1
    return i >= 0 and i + 1 < len(argv) and argv[i + 1] == "all"


def parse_event(ac, scope_all=False):
    docno, html = None, None
    try:
        vp = CACHE / f"viewer_{ac}.json"
        if vp.exists():
            v = json.loads(vp.read_text(encoding="utf-8"))
            docno = FK.orig_docno(v.get("docs"))
    except Exception:
        docno = None
    try:
        if docno and (CACHE / f"body_{docno}.html").exists():
            html = (CACHE / f"body_{docno}.html").read_text(encoding="utf-8")
    except Exception:
        html = None
    return record_from_html(ac, docno, html, scope_all)


def _report(D, recs, scope_all):
    n = len(D)
    nb = sum(1 for r in recs if r.get("has_body"))
    ok = D["parse_ok"].astype(str).str.lower().isin(["true", "1"])
    print(f"이벤트={n} 본문={nb} 투자자표={int((D['n_investors'] > 0).sum())} "
          f"파싱성공={int(ok.sum())} ({float(ok.mean()) if n else 0:.1%})")
    miss = ["new_common", "new_other", "pre_common", "pre_other", "dilution",
            "issue_price", "base_price", "discount_pct", "fund_facility",
            "fund_acquire", "fund_operating", "fund_debt", "fund_securities",
            "fund_other"]
    print("결측: " + ", ".join(f"{c}={int(D[c].isna().sum())}" for c in miss))
    print("inv_type: " + ", ".join(f"{k}={v}"
                                   for k, v in D["inv_type"].value_counts().items()))
    mg = D["mgmt_flag"].astype(str).str.lower().isin(["true", "1"])
    print(f"mgmt_flag True={int(mg.sum())} ({float(mg.mean()) if n else 0:.1%})")
    for c in ("dilution", "discount_pct"):
        q = D[c].quantile([0.10, 0.33, 0.50, 0.67, 0.90])
        print(c + ": " + ", ".join(
            f"{p:g}={v:.4g}" if pd.notna(v) else f"{p:g}=NaN"
            for p, v in zip((10, 33, 50, 67, 90), q)))
    if scope_all:
        print("purpose: " + ", ".join(
            f"{k}={v}" for k, v in D["purpose"].value_counts().items()))
        print("method: " + ", ".join(
            f"{k}={v}" for k, v in D["method"].value_counts().items()))
        print("method × purpose (건수):")
        print(pd.crosstab(D["method"], D["purpose"]).to_string())


def main(scope_all=False):
    pre = "paid_" if scope_all else "third_"
    E = pd.read_csv(OUT / f"{pre}events.csv", dtype={"acptno": str})
    recs = []
    for ac in E["acptno"].astype(str):
        try:
            recs.append(parse_event(ac, scope_all))
        except Exception:
            recs.append(empty_record(ac, None))
    D = pd.DataFrame(recs, columns=COLS)
    OUT.mkdir(parents=True, exist_ok=True)
    D.to_csv(OUT / f"{pre}parsed.csv", index=False, encoding="utf-8")
    _report(D, recs, scope_all)


def opt_val(argv, name):
    """--name v / --name=v → v. 없으면 None."""
    for i, a in enumerate(argv):
        if a == f"--{name}" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
    return None


def main_custom(events_path, out_path):
    E = pd.read_csv(events_path, dtype={"acptno": str})
    if "유형" in E.columns:
        E = E[E["유형"] == "유상"]
    recs = []
    for ac in E["acptno"].astype(str):
        try:
            recs.append(parse_event(ac, True))
        except Exception:
            recs.append(empty_record(ac, None))
    D = pd.DataFrame(recs, columns=COLS)
    outp = Path(out_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    D.to_csv(outp, index=False, encoding="utf-8")
    _report(D, recs, True)


EXAMPLE = (
    "1. 신주의 종류와 수 보통주식 (주) 190,476 기타주식 (주) - "
    "2. 1주당 액면가액 (원) 500 "
    "3. 증자전 발행주식총수 (주) 보통주식 (주) 22,121,745 기타주식 (주) - "
    "4. 자금조달의 목적 시설자금 (원) - 영업양수자금 (원) - 운영자금 (원) 999,999,000 "
    "채무상환자금 (원) - 타법인 증권 취득자금 (원) - 기타자금 (원) - "
    "5. 증자방식 제3자배정증자 "
    "6. 신주 발행가액 보통주식 (원) 5,250 기타주식 (원) - "
    "7. 기준주가 보통주식 (원) 5,826 기타주식 (원) - "
    "7-1. 기준주가 산정방법 최소값 "
    "7-2. 기준주가에 대한 할인율 또는 할증율 (%) -10 "
    "8. 제3자배정에 대한 정관의 근거 정관 제10조"
)

INV_HTML = """<table><thead><tr>
<th>제3자배정 대상자</th><th>회사 또는<br>최대주주와의 관계</th><th>선정경위</th>
<th>증자결정 전후 6월이내 거래내역 및 계획</th><th>배정주식수 (주)</th><th>비 고</th>
</tr></thead><tbody>
<tr><td>김갑수</td><td>최대주주의 특수관계인</td><td>자금조달</td><td>-</td><td>100,000</td><td>-</td></tr>
<tr><td>라온신기술투자조합</td><td>-</td><td>자금조달</td><td>-</td><td>90,476</td><td>-</td></tr>
<tr><td>합계</td><td>-</td><td>-</td><td>-</td><td>190,476</td><td>-</td></tr>
</tbody></table>"""

CORP_HTML = """<table><thead><tr>
<th>명 칭</th><th>출자자수 (명)</th><th>대표이사 (대표조합원)</th>
<th>업무집행자 (업무집행조합원)</th><th>최대주주 (최대출자자)</th>
</tr></thead><tbody>
<tr><td>지엘인터내셔널</td><td>3</td><td>홍길동</td><td>-</td><td>홍길동</td></tr>
</tbody></table>"""


def selfcheck():
    d = numbers_from_text(EXAMPLE)
    assert d["new_common"] == 190476, d
    assert d["new_other"] == 0, d
    assert d["pre_common"] == 22121745, d
    assert d["pre_other"] == 0, d
    assert abs(d["dilution"] - 190476 / 22121745) < 1e-12, d
    assert d["issue_price"] == 5250, d
    assert d["base_price"] == 5826, d
    assert d["discount_pct"] == -10, d
    assert d["fund_operating"] == 999999000, d
    assert d["fund_facility"] == 0 and d["fund_other"] == 0, d
    d2 = numbers_from_text(EXAMPLE.replace("할증율 (%) -10", "할증율 (%) -"))
    assert abs(d2["discount_pct"] - (5250 / 5826 - 1) * 100) < 1e-9, d2
    old = "7. 기준주가에 대한 할인율 또는 할증율 (%) 할인율 10.00% 8. 제3자배정 근거 -"
    assert numbers_from_text(old)["discount_pct"] == -10
    assert numbers_from_text(old.replace("할인율 10.00%", "할증율 5%"))["discount_pct"] == 5
    assert numbers_from_text(old.replace("할인율 10.00%", "10"))["discount_pct"] == -10
    hi = (EXAMPLE.replace("보통주식 (원) 5,250", "보통주식 (원) 5,880")
                 .replace("보통주식 (원) 5,826", "보통주식 (원) 1,761")
                 .replace("할증율 (%) -10", "할증율 (%) 234.0"))
    assert numbers_from_text(hi)["discount_pct"] == 234.0, hi
    lo = EXAMPLE.replace("할증율 (%) -10", "할증율 (%) 10")
    assert numbers_from_text(lo)["discount_pct"] == -10, lo
    invs = investors_from_html(INV_HTML)
    assert [v["name"] for v in invs] == ["김갑수", "라온신기술투자조합"], invs
    assert invs[0]["shares"] == 100000, invs
    assert invs[0]["relation"] == "최대주주의 특수관계인", invs
    assert inv_type_of(invs, []) == "혼합"
    assert inv_type_of(invs[:1], []) == "최대주주·특수관계인"
    assert inv_type_of(invs[1:], []) == "투자조합·펀드"
    corps = corps_from_html(CORP_HTML)
    assert corps == ["지엘인터내셔널"], corps
    solo = [{"name": "주식회사 갑을", "relation": "-", "reason": "", "shares": 10}]
    assert inv_type_of(solo, []) == "전략적 법인"
    nocue = [{"name": "지엘인터내셔널", "relation": "-", "reason": "", "shares": 1}]
    assert inv_type_of(nocue, corps) == "전략적 법인"
    assert inv_type_of(nocue, []) == "개인"
    assert inv_type_of([{"name": "홍길동", "relation": "-", "reason": "", "shares": 1}], []) == "개인"
    assert inv_type_of([], []) == "미상"
    assert mgmt_from_text("이번 증자는 경영권 참여 목적") is True
    assert mgmt_from_text("최대주주 변경 예정") is True
    assert mgmt_from_text("특이사항 없음") is False
    f60 = {"fund_facility": 0, "fund_acquire": 0, "fund_operating": 60,
           "fund_debt": 40, "fund_securities": 0, "fund_other": 0}
    assert fund_total_of(f60) == 100
    assert purpose_of(f60) == "운영"
    fmix = {"fund_facility": 20, "fund_acquire": 0, "fund_operating": 40,
            "fund_debt": 40, "fund_securities": 0, "fund_other": 0}
    assert purpose_of(fmix) == "혼합"
    fzero = dict.fromkeys(FUND_COLS, 0)
    assert fund_total_of(fzero) == 0
    assert purpose_of(fzero) == "현금없음"
    fnan = dict.fromkeys(FUND_COLS, None)
    assert fund_total_of(fnan) is None
    assert purpose_of(fnan) == "현금없음"
    assert method_of("제3자배정증자") == "제3자배정"
    assert method_of("주주배정후 실권주 일반공모") == "주주배정"
    assert method_of("일반공모증자") == "일반공모"
    assert method_of("") == "미상"
    de = numbers_from_text(EXAMPLE)
    assert de["method"] == "제3자배정", de
    assert de["fund_total"] == 999999000, de
    assert de["purpose"] == "운영", de
    print("selfcheck passed")


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--selfcheck" in argv:
        selfcheck()
    else:
        ev, op = opt_val(argv, "events"), opt_val(argv, "out")
        if ev is not None or op is not None:
            if ev is None or op is None:
                print("--events 와 --out 는 함께 지정", file=sys.stderr)
                sys.exit(2)
            main_custom(ev, op)
        else:
            main(scope_all=is_scope_all(argv))
