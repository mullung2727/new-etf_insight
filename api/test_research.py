"""research 라우터 — 자동완성 / 목록(다운로드여부) / 백그라운드 다운로드.

네트워크(네이버)와 디스크는 monkeypatch 로 대체. 실행(api/):
    uv run --with pytest --with httpx pytest test_research.py
"""
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


def test_reports_marks_downloaded(monkeypatch, tmp_path):
    reports = [_report("11", "2026-07-03"), _report("22", "2026-07-01")]
    monkeypatch.setattr(research.dnr, "list_stock_reports", lambda *a, **k: reports)
    monkeypatch.setattr(research.dnr, "DEFAULT_EXPORT_BASE", tmp_path)
    dest = research.dnr.dest_path(tmp_path, "삼성전자", "005930", "2026-07-01", "X증권", "22")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"%PDF-x")  # 22 만 이미 받은 것으로 위장

    r = client.get("/research/stock/005930/reports", params={"name": "삼성전자"})
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 2 and d["already"] == 1
    flags = {x["researchId"]: x["downloaded"] for x in d["reports"]}
    assert flags == {"11": False, "22": True}


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
