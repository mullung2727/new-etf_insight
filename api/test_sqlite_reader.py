"""STEP2 값-정확성 테스트 — etf_insight reader의 SQLite 전환 검증.

test_smoke.py(200-OK 회귀)와 달리 값을 확인한다. 핵심 검증:
  - SQLite 파일을 읽어 /etfs, /etfs/{key}, /countries 정상 값 반환
  - /stats/holdings 의 weight 집계에서 비숫자('-') weight가 **제외**되는지
    (DuckDB TRY_CAST→NULL 동작을 SQLite CAST 가드로 재현. CAST는 junk를 0.0으로
     바꿔 BETWEEN 0 AND 100 을 통과시키므로 가드가 없으면 평균이 오염된다.)

Run from api/:
    uv run --with pytest --with httpx pytest test_sqlite_reader.py
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

import duck
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

_DDL_RECORDS = """
CREATE TABLE etf_records (
    etf_key TEXT PRIMARY KEY, route TEXT, is_pre_listing_etf INTEGER,
    fund_name TEXT, asset_manager TEXT, index_name TEXT, index_provider TEXT,
    index_description TEXT, primary_country TEXT, theme_status TEXT,
    theme_bucket TEXT, structure_tags TEXT, classification_confidence REAL,
    classification_evidence TEXT, holdings_available_in_pdf INTEGER,
    holdings_summary TEXT, keywords TEXT, trend_summary TEXT, missing_info TEXT,
    rcept_no TEXT, rcept_dt TEXT, corp_code TEXT, corp_name TEXT, report_nm TEXT,
    fund_code TEXT, pdf_path TEXT, first_rcept_dt TEXT, revision_count INTEGER,
    db_updated_at TEXT
)
"""
_DDL_HOLDINGS = """
CREATE TABLE etf_holdings (
    etf_key TEXT, seq INTEGER, name TEXT, ticker TEXT, exchange TEXT, weight TEXT,
    PRIMARY KEY (etf_key, seq)
)
"""


@pytest.fixture(autouse=True)
def temp_db(monkeypatch):
    d = Path(tempfile.mkdtemp())
    db = d / "etf_insight.sqlite3"
    con = sqlite3.connect(str(db))
    con.execute(_DDL_RECORDS)
    con.execute(_DDL_HOLDINGS)
    # pre-listing ETF (KR) + 일반 ETF (US, not pre-listing)
    con.execute(
        "INSERT INTO etf_records (etf_key, is_pre_listing_etf, fund_name, primary_country, "
        "theme_status, theme_bucket, structure_tags, classification_confidence, keywords, "
        "missing_info, first_rcept_dt, revision_count) VALUES "
        "('00100000_A001', 1, '알파ETF', 'KR', 'confirmed', 'AI', '[\"lev\"]', 0.9, "
        "'[\"AI\"]', '[]', '20260601', 0)"
    )
    con.execute(
        "INSERT INTO etf_records (etf_key, is_pre_listing_etf, fund_name, primary_country, "
        "first_rcept_dt, revision_count) VALUES "
        "('00200000_B002', 0, '베타ETF', 'US', '20260605', 0)"
    )
    # A001 holdings: 두 정상 weight + 비숫자('-') 1건
    con.executemany(
        "INSERT INTO etf_holdings VALUES (?,?,?,?,?,?)",
        [
            ("00100000_A001", 0, "삼성전자", "005930", "KRX", "10%"),
            ("00100000_A001", 1, "SK하이닉스", "000660", "KRX", "20%"),
            ("00100000_A001", 2, "정체불명", "JUNK", "KRX", "-"),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(duck, "DUCKDB_PATH", db)
    yield
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_list_etfs_returns_both():
    rows = client.get("/etfs").json()
    keys = {r["etf_key"] for r in rows}
    assert keys == {"00100000_A001", "00200000_B002"}


def test_list_etfs_pre_listing_kr_filter():
    rows = client.get("/etfs", params={"pre_listing_only": True, "country": "KR"}).json()
    keys = {r["etf_key"] for r in rows}
    assert keys == {"00100000_A001"}


def test_has_holdings_flag():
    rows = {r["etf_key"]: r for r in client.get("/etfs").json()}
    assert rows["00100000_A001"]["has_holdings"] is True
    assert rows["00200000_B002"]["has_holdings"] is False


def test_structure_tags_parsed():
    rows = {r["etf_key"]: r for r in client.get("/etfs").json()}
    assert rows["00100000_A001"]["structure_tags"] == ["lev"]


def test_countries():
    assert set(client.get("/countries").json()) == {"KR", "US"}


def test_detail_and_holdings():
    detail = client.get("/etfs/00100000_A001")
    assert detail.status_code == 200
    assert detail.json()["fund_name"] == "알파ETF"
    holdings = client.get("/etfs/00100000_A001/holdings").json()
    assert [h["name"] for h in holdings] == ["삼성전자", "SK하이닉스", "정체불명"]


def test_detail_404():
    assert client.get("/etfs/__nope__").status_code == 404


def test_holdings_stats_excludes_nonnumeric_weight():
    """'-' weight 는 평균 집계에서 제외돼야(가드). 결과에 '정체불명' 없음."""
    rows = client.get("/stats/holdings").json()
    by_name = {r["name"]: r for r in rows}
    assert "정체불명" not in by_name
    assert by_name["삼성전자"]["avg_weight"] == 10.0
    assert by_name["SK하이닉스"]["avg_weight"] == 20.0


def test_stats_summary_pre_listing_only():
    s = client.get("/stats/summary").json()
    # is_pre_listing_etf=true 만 집계 → A001 1건
    assert s["total_etfs"] == 1
    assert s["with_any_holdings"] == 1


def test_stats_holdings_by_keys_branch():
    rows = client.get("/stats/holdings", params=[("etf_keys", "00100000_A001")]).json()
    by_name = {r["name"]: r for r in rows}
    assert "정체불명" not in by_name
    assert by_name["삼성전자"]["avg_weight"] == 10.0
