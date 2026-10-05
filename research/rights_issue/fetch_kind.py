"""KIND 증자 일정 수집 (JSON 캐시). stdlib only."""
import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LIST_URL = "https://kind.krx.co.kr/common/stockschedule.do"
VIEWER_URL = "https://kind.krx.co.kr/common/disclsviewer.do"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://kind.krx.co.kr/common/stockschedule.do?method=StockScheduleMain&index=3",
}
SEARCH_URL = "https://kind.krx.co.kr/disclosure/details.do"
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://kind.krx.co.kr/disclosure/details.do?method=searchDetailsMain",
}
SEARCH_KW = {"유상증자결정": "paid", "무상증자결정": "free", "유무상증자결정": "both"}
CACHE = Path(__file__).parent / "cache"
FORMS = {
    "paid": {"method": "searchPaidinCapitalIncrease", "forward": "searchpaidincapitalincrease", "searchMenu": "02"},
    "free": {"method": "searchFreeIssueNewShares", "forward": "searchfreeissuenewshares", "searchMenu": "03"},
}

_net_calls = 0


def clean(s):
    s = re.sub(r"<[^>]*>", "", s)
    s = s.replace("&nbsp;", " ").replace("\xa0", " ")
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return re.sub(r"\s+", " ", s).strip()


def get(url, data=None, headers=None):
    global _net_calls
    if _net_calls:
        time.sleep(0.5)
    _net_calls += 1
    headers = dict(headers if headers is not None else HEADERS)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_list(html):
    m = re.search(r"<thead.*?>(.*?)</thead>", html, re.S | re.I)
    if not m:
        return None
    headers = [clean(t) for t in re.findall(r"<th.*?>(.*?)</th>", m.group(1), re.S | re.I)]
    if not headers:
        return None
    rows = []
    tb = re.search(r"<tbody.*?>(.*?)</tbody>", html, re.S | re.I)
    if tb:
        for tr in re.findall(r"<tr.*?>(.*?)</tr>", tb.group(1), re.S | re.I):
            tds = re.findall(r"<td.*?>(.*?)</td>", tr, re.S | re.I)
            if not tds:
                continue
            row = {h: clean(td) for h, td in zip(headers, tds)}
            am = re.search(r"openDisclsViewer\('(\d{14})'", tr)
            if not am:
                continue
            row["acptno"] = am.group(1)
            if "종목코드" not in row:
                cm = re.search(r"(?:code|isuCd|isu_cd|stkCd|stockCode)\s*[=:\"']+\s*\"?(\d{6})", tr, re.I)
                if not cm:
                    cm = re.search(r"(?<!\d)(\d{6})(?!\d)", re.sub(r"\d{14}", "", tr))
                row["종목코드"] = cm.group(1) if cm else None
            rows.append(row)
    return rows


def parse_search(html):
    """상세검색 결과 파싱 → (행 리스트, 전체 건수). 오류 페이지·표 없음이면 ValueError."""
    if "페이지 오류" in html:
        raise ValueError("search error page")
    trs = re.findall(r"<tr.*?>(.*?)</tr>", html, re.S | re.I)
    if not trs:
        raise ValueError("search no table")
    tm = re.search(r"전체\s*([\d,]+)\s*건", clean(html))
    rows = []
    for tr in trs:
        tds = re.findall(r"<td.*?>(.*?)</td>", tr, re.S | re.I)
        if len(tds) < 4:
            continue
        cells = [clean(td) for td in tds]
        dtm = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", cells[1])
        if not dtm:
            continue
        am = re.search(r"openDisclsViewer\('(\d+)'", tr)
        if not am:
            continue
        dt = dtm.group(0)
        rows.append({"acptno": am.group(1), "datetime": dt, "date": dt[:10],
                     "time": dt[11:16], "company": cells[2], "title": cells[3]})
    total = int(tm.group(1).replace(",", "")) if tm else len(rows)
    return rows, total


def first_announce_date(docs):
    dated = [d for d in docs if d.get("date")]
    if not dated:
        return None
    orig = [d for d in dated if not d["label"].startswith("[정정]")]
    return min(d["date"] for d in (orig or dated))


def parse_viewer(html):
    m = re.search(r"<title.*?>(.*?)</title>", html, re.S | re.I)
    title = clean(m.group(1)) if m else None
    stock_code = None
    hm = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    if hm:
        cm = re.search(r"\((\d{6})\)", clean(hm.group(1)))
        if cm:
            stock_code = cm.group(1)
    docs = []
    sel = re.search(r"<select[^>]*id=[\"']mainDoc[\"'][^>]*>(.*?)</select>", html, re.S | re.I)
    if sel:
        for om in re.finditer(r"<option[^>]*value\s*=\s*[\"']([^\"']*)[\"'][^>]*>(.*?)</option>", sel.group(1), re.S | re.I):
            val, text = om.group(1).strip(), clean(om.group(2))
            if not val:
                continue
            dm = re.search(r"\((\d{4})\.(\d{2})\.(\d{2})\)", text)
            if dm:
                docs.append({"docno": val.split("|")[0], "label": clean(text[: dm.start()]),
                             "date": f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"})
            else:
                docs.append({"docno": val.split("|")[0], "label": text, "date": None})
    return title, stock_code, docs, first_announce_date(docs)


def parse_setpath(text):
    m = re.search(r"""setPath\(\s*['"][^'"]*['"]\s*,\s*['"]([^'"]+)['"]""", text)
    return m.group(1) if m else None


def load(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def save(p, obj):
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def save_text(p, text):
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def is_stale(cache_obj, period_is_current):
    if not period_is_current:
        return False
    if not cache_obj or not cache_obj.get("fetched_at"):
        return True
    today = time.strftime("%Y-%m-%d", time.localtime())
    return cache_obj["fetched_at"][:10] < today


KW_TO_KEYWORD = {v: k for k, v in SEARCH_KW.items()}


def match_search_title(title, substr):
    if not title or substr not in title:
        return False
    if title.startswith("[정정]"):
        return False
    for w in ("종속회사", "자회사", "철회"):
        if w in title:
            return False
    return True


def fetch_list(kind, year, month, refresh):
    yyyymm = f"{year}{month:02d}"
    p = CACHE / f"list_{kind}_{yyyymm}.json"
    if not refresh:
        hit = load(p)
        cur = time.strftime("%Y%m", time.localtime())
        if hit is not None and not is_stale(hit, yyyymm >= cur):
            return hit["rows"], True
    form = dict(FORMS[kind], selYear=str(year), selMonth=f"{month:02d}", nowYear=str(year), nowMonth=f"{month:02d}")
    try:
        rows = parse_list(get(LIST_URL, form))
    except Exception as e:
        print(f"[warn] list {kind} {yyyymm} fetch fail: {e}")
        return None, False
    if rows is None:
        print(f"[warn] list {kind} {yyyymm}: no thead, not cached")
        return None, False
    save(p, {"kind": kind, "year": year, "month": month, "fetched_at": now(), "rows": rows})
    return rows, False


def fetch_viewer(acptno, refresh):
    p = CACHE / f"viewer_{acptno}.json"
    if not refresh:
        hit = load(p)
        if hit is not None:
            return hit, True
    try:
        title, stock_code, docs, first = parse_viewer(get(f"{VIEWER_URL}?method=search&acptno={acptno}"))
    except Exception as e:
        print(f"[warn] viewer {acptno} fetch fail: {e}")
        return None, False
    if not docs:
        print(f"[warn] viewer {acptno}: no docs, not cached")
        return None, False
    v = {"acptno": acptno, "title": title, "stock_code": stock_code, "docs": docs, "first_announce_date": first, "fetched_at": now()}
    save(p, v)
    return v, False


def orig_docno(docs):
    docs = docs or []
    dated = [d for d in docs if d.get("date")]
    orig = [d for d in dated if not (d.get("label") or "").startswith("[정정]")]
    if orig:
        return min(orig, key=lambda d: d["date"]).get("docno")
    return docs[0].get("docno") if docs else None


def fetch_body(acptno, refresh):
    p = CACHE / f"viewer_{acptno}.json"
    v = load(p)
    if v is None:
        v, _ = fetch_viewer(acptno, refresh)
    if not v or not v.get("docs"):
        print(f"[warn] body {acptno}: no viewer/docs")
        return None
    docno = orig_docno(v["docs"])
    if not docno:
        print(f"[warn] body {acptno}: no docno")
        return None
    bp = CACHE / f"body_{docno}.html"
    if not refresh and bp.exists():
        return docno, bp.read_text(encoding="utf-8")
    try:
        body_url = parse_setpath(get(f"{VIEWER_URL}?method=searchContents&docNo={docno}"))
    except Exception as e:
        print(f"[warn] body {acptno}/{docno} contents fail: {e}")
        return None
    if not body_url:
        print(f"[warn] body {acptno}/{docno}: setPath not found")
        return None
    try:
        html = get(body_url)
    except Exception as e:
        print(f"[warn] body {acptno}/{docno} fetch fail: {e}")
        return None
    if not html.strip():
        print(f"[warn] body {acptno}/{docno}: empty body")
        return None
    save_text(bp, html)
    return docno, html


def fetch_search(keyword, year, refresh):
    kw = SEARCH_KW[keyword]
    p = CACHE / f"search_{kw}_{year}.json"
    if not refresh:
        hit = load(p)
        cur_year = int(time.strftime("%Y", time.localtime()))
        if hit is not None and not is_stale(hit, year >= cur_year):
            return hit["rows"], True
    today = time.strftime("%Y-%m-%d", time.localtime())
    to_date = min(f"{year}-12-31", today)
    rows = []
    page = 1
    try:
        while True:
            form = {"method": "searchDetailsSub", "currentPageSize": "100",
                    "pageIndex": str(page), "orderMode": "1", "orderStat": "D",
                    "forward": "details_sub", "searchCodeType": "", "repIsuSrtCd": "",
                    "fromDate": f"{year}-01-01", "toDate": to_date, "reportNm": keyword}
            pr, total = parse_search(get(SEARCH_URL, form, SEARCH_HEADERS))
            rows.extend(pr)
            if not pr or page >= (total + 99) // 100:
                break
            page += 1
    except Exception as e:
        print(f"[warn] search {keyword} {year} fetch fail: {e}")
        return None, False
    save(p, {"keyword": keyword, "kw": kw, "year": year, "fetched_at": now(), "rows": rows})
    return rows, False


def selfcheck():
    html = """<table><thead><tr><th>기준일</th><th>회사명</th><th>증자구분</th><th>원문보기</th></tr></thead>
<tbody><tr><td>2024.03.15</td><td><a href="/x?code=005930">테스트&nbsp;전자</a></td><td>주주배정</td>
<td><a href="#" onclick="openDisclsViewer('20240315000123',''); return false;">원문</a></td></tr></tbody></table>"""
    rows = parse_list(html)
    assert rows is not None and len(rows) == 1, rows
    r = rows[0]
    assert r["기준일"] == "2024.03.15", r
    assert r["회사명"] == "테스트 전자", r
    assert r["acptno"] == "20240315000123", r
    assert r["종목코드"] == "005930", r
    empty = """<table><thead><tr><th>기준일</th></tr></thead><tbody><tr><td colspan="7">조회된 내역이 없습니다.</td></tr></tbody></table>"""
    assert parse_list(empty) == []
    v = """<html><head><title>[테스트] [정정]유상증자결정</title></head><body>
<h1 class="ttl type-99 fleft">테스트 (123456)</h1>
<select id="mainDoc"><option value=''>본문선택</option>
<option value='20240320000111|N'>유상증자결정 (2024.03.20)</option>
<option value='20240305000222|Y'>[정정]유상증자결정 (2024.03.05)</option>
<option value='20240325000333|Y'>[정정]유상증자결정 (2024.03.25)</option></select></body></html>"""
    title, stock_code, docs, first = parse_viewer(v)
    assert title == "[테스트] [정정]유상증자결정", title
    assert stock_code == "123456", stock_code
    assert len(docs) == 3, docs
    assert docs[0] == {"docno": "20240320000111", "label": "유상증자결정", "date": "2024-03-20"}, docs
    assert first == "2024-03-20", first
    _, _, docs2, first2 = parse_viewer(v.replace("유상증자결정 (2024.03.20)", "[정정]유상증자결정 (2024.03.20)"))
    assert first2 == "2024-03-05", (docs2, first2)
    _, sc_none, _, _ = parse_viewer(re.sub(r"<h1.*?</h1>\n?", "", v))
    assert sc_none is None, sc_none
    s = """<div>전체 93 건</div><table><thead><tr><th>번호</th><th>시간</th><th>회사명</th><th>공시제목</th><th>제출인</th></tr></thead>
<tbody><tr><td>16</td><td>2024-02-14 16:34</td><td>풍산홀딩스</td>
<td><a href="#" onclick="openDisclsViewer('20241220000669',''); return false;">무상증자결정</a></td><td>풍산홀딩스</td></tr></tbody></table>"""
    srows, stotal = parse_search(s)
    assert len(srows) == 1 and stotal == 93 and (srows[0]["time"], srows[0]["company"], srows[0]["title"], srows[0]["acptno"]) == ("16:34", "풍산홀딩스", "무상증자결정", "20241220000669")
    today = time.strftime("%Y-%m-%d", time.localtime())
    yday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    assert is_stale({"fetched_at": "2000-01-01T00:00:00+0900"}, False) is False
    assert is_stale({}, False) is False
    assert is_stale({"fetched_at": f"{yday}T00:00:00+0900"}, True) is True
    assert is_stale({"fetched_at": f"{today}T00:00:00+0900"}, True) is False
    assert is_stale({}, True) is True
    assert match_search_title("[정정]유상증자결정(제3자배정)", "제3자배정") is False
    assert match_search_title("유상증자결정(종속회사의 주요경영사항)", "유상증자결정") is False
    assert match_search_title("기타 주요경영사항(제3자배정 유상증자 결정 철회)", "제3자배정") is False
    assert match_search_title("유상증자결정(제3자배정)", "제3자배정") is True
    sp = """<script>top.setPath('/common/toc.do?x=1','https://kind.krx.co.kr/external/2025/01/06/000329/20250106001003/11306.htm','000329');</script>"""
    assert parse_setpath(sp) == "https://kind.krx.co.kr/external/2025/01/06/000329/20250106001003/11306.htm"
    print("selfcheck passed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int)
    ap.add_argument("--month", type=int)
    ap.add_argument("--kind", default="both", choices=["paid", "free", "both"])
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--search", action="store_true")
    ap.add_argument("--search-viewers", action="store_true")
    ap.add_argument("--kw", choices=["paid", "free", "both"])
    ap.add_argument("--title")
    ap.add_argument("--bodies-from-csv")
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        selfcheck()
        return
    if a.search_viewers:
        if not a.year:
            ap.error("--search-viewers needs --year")
        if not a.kw:
            ap.error("--search-viewers needs --kw")
        if not a.title:
            ap.error("--search-viewers needs --title")
        rows, _ = fetch_search(KW_TO_KEYWORD[a.kw], a.year, a.refresh)
        targets = [r for r in (rows or []) if match_search_title(r.get("title", ""), a.title)]
        hits = news = fails = no_code = 0
        for r in targets:
            ac = r.get("acptno")
            if not ac:
                fails += 1
                continue
            v, cached = fetch_viewer(ac, a.refresh)
            if v is None:
                fails += 1
            else:
                if cached:
                    hits += 1
                else:
                    news += 1
                if not v.get("stock_code"):
                    no_code += 1
        print(f"[search-viewers] kw={a.kw} year={a.year} title={a.title} targets={len(targets)} viewer hit={hits} new={news} fail={fails} no_stock_code={no_code}")
        return
    if a.search:
        if not a.year:
            ap.error("--search needs --year")
        hits = news = 0
        for keyword in ("유상증자결정", "무상증자결정", "유무상증자결정"):
            rows, cached = fetch_search(keyword, a.year, a.refresh)
            if cached:
                hits += 1
            else:
                news += 1
            print(f"[{keyword}] {a.year} rows={len(rows) if rows else 0} ({'hit' if cached else 'new'})")
        print(f"search cache hit={hits} new={news}")
        return
    if a.bodies_from_csv:
        csv_path = Path(a.bodies_from_csv)
        if not csv_path.exists():
            ap.error(f"--bodies-from-csv file not found: {csv_path}")
        with csv_path.open(encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            if not rd.fieldnames or "acptno" not in rd.fieldnames:
                ap.error("--bodies-from-csv csv needs acptno column")
            acptnos = [((r.get("acptno") or "").strip()) for r in rd]
        acptnos = [x for x in acptnos if x]
        hits = news = fails = 0
        for ac in acptnos:
            v = load(CACHE / f"viewer_{ac}.json")
            docno = orig_docno(v.get("docs")) if v else None
            pre_hit = bool(docno and not a.refresh and (CACHE / f"body_{docno}.html").exists())
            res = fetch_body(ac, a.refresh)
            if res is None:
                fails += 1
            elif pre_hit:
                hits += 1
            else:
                news += 1
        print(f"[bodies] csv={a.bodies_from_csv} targets={len(acptnos)} hit={hits} new={news} fail={fails}")
        return
    if not a.year or not a.month:
        ap.error("--year/--month required")
    kinds = ["paid", "free"] if a.kind == "both" else [a.kind]
    hits = news = 0
    data = {}
    for k in kinds:
        rows, _ = fetch_list(k, a.year, a.month, a.refresh)
        viewers = {}
        for r in rows or []:
            ac = r.get("acptno")
            if not ac:
                continue
            v, cached = fetch_viewer(ac, a.refresh)
            if cached:
                hits += 1
            else:
                news += 1
            if v:
                viewers[ac] = v
        data[k] = (rows, viewers)
    for k in kinds:
        rows, viewers = data[k]
        print(f"[{k}] {a.year}-{a.month:02d} rows={len(rows) if rows else 0}")
        for r in (rows or [])[:3]:
            line = f"{r.get('회사명', '-')} | {r.get('기준일', r.get('배정기준일', '-'))}"
            if k == "paid":
                line += f" | {r.get('증자구분', '-')}"
            v = viewers.get(r.get("acptno") or "")
            print(f"  {line} | {r.get('acptno', '-')} | {v['first_announce_date'] if v else '-'} | {(v.get('stock_code') or '-') if v else '-'}")
    print(f"viewer cache hit={hits} new={news}")


if __name__ == "__main__":
    main()
