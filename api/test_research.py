"""research 라우터 — 자동완성 / 목록(다운로드여부) / 백그라운드 다운로드.

네트워크(네이버)와 디스크는 monkeypatch 로 대체. 실행(api/):
    uv run --with pytest --with httpx pytest test_research.py
"""
import sqlite3
import time

from fastapi.testclient import TestClient

from main import app
from routers import research

from report_metrics import catalog

client = TestClient(app)


def _report(rid, date, broker="X증권", title="t"):
    return {"researchId": rid, "itemCode": "005930", "itemName": "삼성전자",
            "brokerName": broker, "title": title, "writeDate": date,
            "pdf_url": f"http://x/{rid}.pdf"}


def test_search_returns_candidates():
    r = client.get("/research/search", params={"q": "삼성"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def _seed_local_reports(base):
    d = base / "피에스케이_319660"
    d.mkdir(parents=True, exist_ok=True)
    (d / "2026-08-18_신한투자증권_20260818_company_950573000.pdf").write_bytes(b"%PDF-a")
    (d / "2026-09-01_BNK투자증권_20260901_company_1.pdf").write_bytes(b"%PDF-b")
    (d / "notes.txt").write_text("x")
    (d / "bad.pdf").write_bytes(b"%PDF-bad")
    other = base / "피에스케이홀딩스_031980"
    other.mkdir(parents=True, exist_ok=True)
    (other / "2026-08-18_X증권_k.pdf").write_bytes(b"%PDF-k")


def _seed_facts_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE report_api_facts (pdf_key TEXT PRIMARY KEY,"
                " research_id TEXT NOT NULL, stock_code TEXT NOT NULL, title TEXT)")
    con.execute("INSERT INTO report_api_facts (pdf_key, research_id, stock_code, title)"
                " VALUES (?,?,?,?)",
                ("20260818_company_950573000", "123", "319660", "제목A"))
    con.commit()
    con.close()


def _forbid_source(monkeypatch):
    def _raise(*a, **k):
        raise AssertionError("원천 조회 금지")
    monkeypatch.setattr(research.dnr, "list_stock_reports", _raise)


def test_reports_lists_local_pdfs(monkeypatch, tmp_path):
    _forbid_source(monkeypatch)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    db = tmp_path / "facts.sqlite3"
    _seed_facts_db(db)
    monkeypatch.setattr(research, "_FACTS_DB", db)
    _seed_local_reports(tmp_path)

    r = client.get("/research/stock/319660/reports", params={"name": "피에스케이"})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 2 and d["already"] == 2
    first, second = d["reports"]
    assert (first["writeDate"], first["brokerName"]) == ("2026-09-01", "BNK투자증권")
    assert first["researchId"] == first["pdfKey"] == "20260901_company_1"
    assert first["title"] == ""
    assert second["researchId"] == "123" and second["title"] == "제목A"
    assert second["pdfKey"] == "20260818_company_950573000"
    assert all(x["downloaded"] for x in d["reports"])


def test_reports_since_filter(monkeypatch, tmp_path):
    _forbid_source(monkeypatch)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research, "_FACTS_DB", tmp_path / "none.sqlite3")
    _seed_local_reports(tmp_path)

    r = client.get("/research/stock/319660/reports",
                   params={"name": "피에스케이", "since": "2026-08-20"})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 1
    assert d["reports"][0]["brokerName"] == "BNK투자증권"


def test_reports_without_facts_db(monkeypatch, tmp_path):
    _forbid_source(monkeypatch)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research, "_FACTS_DB", tmp_path / "none.sqlite3")
    _seed_local_reports(tmp_path)

    r = client.get("/research/stock/319660/reports", params={"name": "피에스케이"})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 2
    assert all(x["title"] == "" for x in d["reports"])


def test_reports_invalid_code_422(monkeypatch, tmp_path):
    _forbid_source(monkeypatch)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    r = client.get("/research/stock/abc/reports")
    assert r.status_code == 422


def test_reports_broker_name_with_underscore(monkeypatch, tmp_path):
    """sanitize('A/B증권')='A_B증권' — 첫 '_' 분할이면 증권사 'A', 키 'B증권_...' 로 깨진다 (CodeRabbit PR #34)."""
    _forbid_source(monkeypatch)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research, "_FACTS_DB", tmp_path / "none.sqlite3")
    d = tmp_path / "현대차_005380"
    d.mkdir(parents=True)
    (d / "2026-09-23_A_B증권_20260923_company_77.pdf").write_bytes(b"%PDF-x")
    rep = client.get("/research/stock/005380/reports", params={"name": "현대차"}).json()["reports"][0]
    assert (rep["brokerName"], rep["pdfKey"]) == ("A_B증권", "20260923_company_77")


def test_split_broker_key_prefers_known_fact_key():
    assert research._split_broker_key("A_B증권_x_y", {"x_y"}) == ("A_B증권", "x_y")
    assert research._split_broker_key("X증권_k", set()) == ("X증권", "k")  # 구 규칙 유지
    assert research._split_broker_key("nokey", set()) is None


def test_download_job_range_empty_is_done_not_error(monkeypatch, tmp_path):
    """원천은 정상인데 기간 안에 0건 → done(total 0), '원천 미지원' 오류로 표시하지 않음."""
    def fake_list(code, name, since=None, until=None, max_pages=20, **k):
        return [] if (since or until) else [_report("1", "2026-01-02")]
    monkeypatch.setattr(research.dnr, "list_stock_reports", fake_list)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research.dnr, "REQUEST_SLEEP", 0)
    r = client.post("/research/stock/005930/download",
                    json={"name": "삼성전자", "since": "2026-09-01"})
    jid = r.json()["job_id"]
    for _ in range(60):
        s = client.get(f"/research/jobs/{jid}").json()
        if s["status"] != "running":
            break
        time.sleep(0.05)
    assert s["status"] == "done" and s["total"] == 0


def test_download_job_source_empty_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(research.dnr, "list_stock_reports", lambda *a, **k: [])
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research.dnr, "REQUEST_SLEEP", 0)

    r = client.post("/research/stock/005930/download", json={"name": "삼성전자"})
    assert r.status_code == 200
    jid = r.json()["job_id"]
    for _ in range(60):
        s = client.get(f"/research/jobs/{jid}").json()
        if s["status"] != "running":
            break
        time.sleep(0.05)
    assert s["status"] == "error"
    assert "원천" in (s["error"] or "")


def test_pdf_serves_existing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    dest = research.dnr.dest_path(tmp_path, "삼성전자", "005930", "2026-07-01", "X증권", "22")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"%PDF-x")

    r = client.get("/research/stock/005930/reports/22/pdf", params={
        "name": "삼성전자", "writeDate": "2026-07-01", "brokerName": "X증권", "pdfKey": "22",
    })
    assert r.status_code == 200
    assert r.content == b"%PDF-x"
    assert r.headers["content-type"] == "application/pdf"


def test_pdf_found_when_folder_name_differs(monkeypatch, tmp_path):
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    dest = research.dnr.dest_path(tmp_path, "옛이름", "005930", "2026-07-01", "X증권", "22")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"%PDF-x")

    r = client.get("/research/stock/005930/reports/22/pdf", params={
        "name": "새이름", "writeDate": "2026-07-01", "brokerName": "X증권", "pdfKey": "22",
    })
    assert r.status_code == 200


def test_pdf_missing_file_404(monkeypatch, tmp_path):
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    r = client.get("/research/stock/005930/reports/22/pdf", params={
        "name": "삼성전자", "writeDate": "2026-07-01", "brokerName": "X증권", "pdfKey": "22",
    })
    assert r.status_code == 404


def test_pdf_path_traversal_blocked(monkeypatch, tmp_path):
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    # dest_path 구조상 파일명 첫/끝 세그먼트는 접두/접미가 붙어 무력화되므로,
    # 중간 세그먼트만으로 실제 tmp_path 밖(부모)까지 나가는 key를 구성해 검증.
    escape_target = tmp_path.parent / "escaped.pdf"
    escape_target.write_bytes(b"%PDF-secret")
    try:
        r = client.get("/research/stock/005930/reports/22/pdf", params={
            "name": "삼성전자", "writeDate": "2026-07-01", "brokerName": "X증권",
            "pdfKey": "x/../../../escaped",
        })
        assert r.status_code == 404
    finally:
        escape_target.unlink(missing_ok=True)


def test_download_job_lifecycle(monkeypatch, tmp_path):
    reports = [_report("11", "2026-07-03"), _report("22", "2026-07-01")]

    def fake_dl(url, dest, **k):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF")
        return True

    monkeypatch.setattr(research.dnr, "list_stock_reports", lambda *a, **k: reports)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research.dnr, "download_pdf", fake_dl)
    monkeypatch.setattr(research.dnr, "REQUEST_SLEEP", 0)

    r = client.post("/research/stock/005930/download", json={"name": "삼성전자"})
    assert r.status_code == 200
    jid = r.json()["job_id"]
    for _ in range(60):
        s = client.get(f"/research/jobs/{jid}").json()
        if s["status"] != "running":
            break
        time.sleep(0.05)
    assert s["status"] == "done"
    assert s["downloaded"] == 2 and s["skipped"] == 0 and s["total"] == 2


def test_rerun_skips_downloaded(monkeypatch, tmp_path):
    reports = [_report("11", "2026-07-03")]
    monkeypatch.setattr(research.dnr, "list_stock_reports", lambda *a, **k: reports)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    # 이미 받은 파일 생성
    dest = research.dnr.dest_path(tmp_path, "삼성전자", "005930", "2026-07-03", "X증권", "11")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"%PDF")
    monkeypatch.setattr(research.dnr, "download_pdf",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("이미 받은 건 재다운 금지")))
    monkeypatch.setattr(research.dnr, "REQUEST_SLEEP", 0)

    jid = client.post("/research/stock/005930/download", json={"name": "삼성전자"}).json()["job_id"]
    for _ in range(60):
        s = client.get(f"/research/jobs/{jid}").json()
        if s["status"] != "running":
            break
        time.sleep(0.05)
    assert s["status"] == "done" and s["skipped"] == 1 and s["downloaded"] == 0


def _await_done(jid):
    for _ in range(60):
        s = client.get(f"/research/jobs/{jid}").json()
        if s["status"] != "running":
            return s
        time.sleep(0.05)
    return s


def test_download_only_selected_reports(monkeypatch, tmp_path):
    reports = [_report("11", "2026-07-03"), _report("22", "2026-07-01")]
    called = []

    def fake_dl(url, dest, **k):
        called.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"%PDF")
        return True

    monkeypatch.setattr(research.dnr, "list_stock_reports", lambda *a, **k: reports)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research.dnr, "download_pdf", fake_dl)
    monkeypatch.setattr(research.dnr, "REQUEST_SLEEP", 0)

    jid = client.post("/research/stock/005930/download",
                      json={"name": "삼성전자", "researchIds": ["22"]}).json()["job_id"]
    s = _await_done(jid)
    assert s["status"] == "done"
    assert s["total"] == 1 and s["downloaded"] == 1
    assert called == ["http://x/22.pdf"]  # 11 은 다운 안 함


def test_selected_download_skips_existing(monkeypatch, tmp_path):
    reports = [_report("11", "2026-07-03"), _report("22", "2026-07-01")]
    monkeypatch.setattr(research.dnr, "list_stock_reports", lambda *a, **k: reports)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    dest = research.dnr.dest_path(tmp_path, "삼성전자", "005930", "2026-07-01", "X증권", "22")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"%PDF")  # 22 이미 받음
    monkeypatch.setattr(research.dnr, "download_pdf",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("재다운 금지")))
    monkeypatch.setattr(research.dnr, "REQUEST_SLEEP", 0)

    jid = client.post("/research/stock/005930/download",
                      json={"name": "삼성전자", "researchIds": ["22"]}).json()["job_id"]
    s = _await_done(jid)
    assert s["status"] == "done" and s["total"] == 1 and s["skipped"] == 1 and s["downloaded"] == 0


def test_selected_ids_missing_from_query_ignored(monkeypatch, tmp_path):
    reports = [_report("11", "2026-07-03")]
    monkeypatch.setattr(research.dnr, "list_stock_reports", lambda *a, **k: reports)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research.dnr, "download_pdf",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("호출 금지")))
    monkeypatch.setattr(research.dnr, "REQUEST_SLEEP", 0)

    jid = client.post("/research/stock/005930/download",
                      json={"name": "삼성전자", "researchIds": ["not-found"]}).json()["job_id"]
    s = _await_done(jid)
    assert s["status"] == "done"
    assert s["total"] == 0 and s["downloaded"] == 0 and s["skipped"] == 0


def test_max_3_concurrent(monkeypatch):
    research._JOBS.clear()
    for i in range(3):
        research._JOBS[f"j{i}"] = {"status": "running"}
    monkeypatch.setattr(research, "_resolve_name", lambda c: "x")
    r = client.post("/research/stock/000660/download", json={})
    assert r.status_code == 429
    research._JOBS.clear()


# --- 카탈로그 기반 API (§5, §6-6) ---

_NAMES = {"319660": "피에스케이", "031980": "피에스케이홀딩스", "005930": "삼성전자"}


def _catalog_setup(monkeypatch, tmp_path):
    db = tmp_path / "cat.sqlite3"
    con = sqlite3.connect(db)
    catalog.init_catalog(con)
    con.execute("CREATE TABLE IF NOT EXISTS report_api_facts (pdf_key TEXT PRIMARY KEY,"
                " research_id TEXT NOT NULL, stock_code TEXT NOT NULL, title TEXT)")
    con.commit()
    con.close()
    monkeypatch.setattr(research, "_FACTS_DB", db)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE",
                        tmp_path / "exports" / "stock_reports")
    monkeypatch.setattr(research, "_resolve_names",
                        lambda codes: {c: _NAMES.get(c, c) for c in codes})
    return db


def _add_doc(db, dtype="company", title="T", broker="B", date="2026-09-29",
             pkey="k", sha="s", status="saved", sub=None, pdf_path=None):
    con = sqlite3.connect(db)
    try:
        doc_id, _ = catalog.upsert_document(
            con, document_type=dtype, title=title, subcategory=sub,
            broker=broker, published_date=date, pdf_path=pdf_path,
            sha256=f"{sha}-{pkey}", pdf_key=pkey,
            file_status=status)
        con.commit()
        return doc_id
    finally:
        con.close()


def _link(db, doc_id, code, rel="primary", pf=None, pt=None):
    con = sqlite3.connect(db)
    try:
        catalog.upsert_document_stock(
            con, document_id=doc_id, stock_code=code,
            relation_type=rel, method="test", page_from=pf, page_to=pt)
        con.commit()
    finally:
        con.close()


def _add_fact(db, pkey, rid, code, title):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO report_api_facts (pdf_key, research_id, stock_code, title)"
                " VALUES (?,?,?,?)", (pkey, rid, code, title))
    con.commit()
    con.close()


def test_stock_reports_catalog_primary_and_mentions(monkeypatch, tmp_path):
    db = _catalog_setup(monkeypatch, tmp_path)
    c1 = _add_doc(db, dtype="company", title="컴퍼니T", broker="신한투자증권",
                  date="2026-09-29", pkey="k1", pdf_path="stock_reports/x/k1.pdf")
    _add_fact(db, "k1", "777", "319660", "팩트T")
    _link(db, c1, "319660", "primary")
    i1 = _add_doc(db, dtype="industry", title="섹터T", broker="A증권",
                  date="2026-09-29", pkey="ik1", sub="반도체",
                  pdf_path="sector_reports/industry/i1.pdf")
    _link(db, i1, "319660", "primary", 34, 41)
    m1 = _add_doc(db, dtype="market", title="시황T", broker="B증권",
                  date="2026-09-28", pkey="mk1",
                  pdf_path="sector_reports/market/m1.pdf")
    _link(db, m1, "319660", "mention")

    r = client.get("/research/stock/319660/reports", params={"name": "피에스케이"})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 2 and d["already"] == 2
    first, second = d["reports"]
    assert first["documentType"] == "company"  # 같은 날짜면 company 먼저
    assert first["researchId"] == "777" and first["title"] == "컴퍼니T"
    assert second["documentType"] == "industry"
    assert second["researchId"] == f"doc-{i1}"
    assert second["pageFrom"] == 34 and second["pageTo"] == 41
    assert second["subcategory"] == "반도체"
    assert second["relationType"] == "primary"

    r2 = client.get("/research/stock/319660/reports",
                    params={"name": "피에스케이", "include_mentions": "true"})
    assert r2.json()["total"] == 3


def test_stock_reports_other_code_not_mixed(monkeypatch, tmp_path):
    db = _catalog_setup(monkeypatch, tmp_path)
    h1 = _add_doc(db, dtype="company", title="홀딩스T", broker="X증권",
                  date="2026-09-29", pkey="hk1")
    _link(db, h1, "031980", "primary")
    r = client.get("/research/stock/319660/reports", params={"name": "피에스케이"})
    assert r.json()["total"] == 0
    r2 = client.get("/research/stock/031980/reports", params={"name": "피에스케이홀딩스"})
    assert r2.json()["total"] == 1


def test_stock_reports_excludes_not_pdf(monkeypatch, tmp_path):
    db = _catalog_setup(monkeypatch, tmp_path)
    b1 = _add_doc(db, dtype="company", title="깨짐", broker="X증권",
                  date="2026-09-29", pkey="bk1", status="not_pdf")
    _link(db, b1, "319660", "primary")
    g1 = _add_doc(db, dtype="company", title="정상", broker="Y증권",
                  date="2026-09-29", pkey="gk1")
    _link(db, g1, "319660", "primary")
    d = client.get("/research/stock/319660/reports",
                   params={"name": "피에스케이"}).json()
    assert d["total"] == 1
    assert d["reports"][0]["title"] == "정상"


def test_documents_list_filters_and_primary_stocks(monkeypatch, tmp_path):
    db = _catalog_setup(monkeypatch, tmp_path)
    c1 = _add_doc(db, dtype="company", title="종목리포트", broker="C증권",
                  date="2026-09-29", pkey="ck1")
    _link(db, c1, "319660", "primary")
    i1 = _add_doc(db, dtype="industry", title="반도체 전망", broker="A증권",
                  date="2026-09-29", pkey="ik1", sub="반도체")
    _link(db, i1, "319660", "primary", 34, 41)
    _link(db, i1, "005930", "primary", 10, 12)
    i2 = _add_doc(db, dtype="industry", title="에너지 동향", broker="B증권",
                  date="2026-09-28", pkey="ik2", sub="에너지")
    _link(db, i2, "031980", "primary", 5, 6)

    d = client.get("/research/documents").json()
    assert d["total"] == 2  # company 제외
    d2 = client.get("/research/documents", params={"types": "industry"}).json()
    assert d2["total"] == 2
    d3 = client.get("/research/documents", params={"q": "반도체"}).json()
    assert d3["total"] == 1 and d3["items"][0]["title"] == "반도체 전망"
    d4 = client.get("/research/documents", params={"limit": "1"}).json()
    assert d4["total"] == 2 and len(d4["items"]) == 1
    first = d["items"][0]
    assert first["documentType"] == "industry"
    codes = [s["code"] for s in first["primaryStocks"]]
    assert codes == ["005930", "319660"]  # page_from 오름차순
    assert first["primaryStocks"][1]["name"] == "피에스케이"
    assert first["primaryStocks"][1]["pageFrom"] == 34


def test_documents_invalid_types_422(monkeypatch, tmp_path):
    _catalog_setup(monkeypatch, tmp_path)
    r = client.get("/research/documents", params={"types": "bogus"})
    assert r.status_code == 422


def test_document_pdf_ok_traversal_and_missing(monkeypatch, tmp_path):
    db = _catalog_setup(monkeypatch, tmp_path)
    base = tmp_path / "exports"
    pdf = base / "sector_reports" / "industry" / "t.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(b"%PDF-ok")
    g1 = _add_doc(db, dtype="industry", title="T", broker="A증권",
                  date="2026-09-29", pkey="gk1",
                  pdf_path="sector_reports/industry/t.pdf")
    r = client.get(f"/research/documents/{g1}/pdf")
    assert r.status_code == 200 and r.content == b"%PDF-ok"
    assert r.headers["content-type"] == "application/pdf"

    e1 = _add_doc(db, dtype="industry", title="E", broker="A증권",
                  date="2026-09-29", pkey="ek1", pdf_path="../../x")
    assert client.get(f"/research/documents/{e1}/pdf").status_code == 404
    assert client.get("/research/documents/999999/pdf").status_code == 404


def test_documents_filters(monkeypatch, tmp_path):
    db = _catalog_setup(monkeypatch, tmp_path)
    _add_doc(db, dtype="industry", title="T1", broker="B증권",
             date="2026-09-29", pkey="fk1", sub="반도체")
    _add_doc(db, dtype="market", title="T2", broker="A증권",
             date="2026-09-29", pkey="fk2")
    _add_doc(db, dtype="company", title="T3", broker="C증권",
             date="2026-09-29", pkey="fk3")
    d = client.get("/research/documents/filters").json()
    assert d["brokers"] == ["A증권", "B증권"]
    assert d["subcategories"] == ["반도체"]
    assert d["types"] == ["industry", "market"]


def test_stock_reports_falls_back_when_no_catalog_db(monkeypatch, tmp_path):
    _forbid_source(monkeypatch)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    monkeypatch.setattr(research, "_FACTS_DB", tmp_path / "none.sqlite3")
    _seed_local_reports(tmp_path)
    d = client.get("/research/stock/319660/reports",
                   params={"name": "피에스케이"}).json()
    assert d["total"] == 2
    assert d["reports"][0]["documentType"] == "company"
    assert d["reports"][0]["documentId"] == 0
