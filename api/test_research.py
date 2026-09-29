"""research 라우터 — 자동완성 / 목록(다운로드여부) / 백그라운드 다운로드.

네트워크(네이버)와 디스크는 monkeypatch 로 대체. 실행(api/):
    uv run --with pytest --with httpx pytest test_research.py
"""
import sqlite3
import time

from fastapi.testclient import TestClient

from main import app
from routers import research

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
