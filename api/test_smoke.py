"""Smoke tests — boot the app in-process and hit core endpoints.

Goal: confirm the refactored duck/etfs/stats code path still returns 200
(no crash, no broken SQL). Not a value-correctness check.

Run from api/:
    uv run --with pytest --with httpx pytest test_smoke.py
"""

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["db_exists"] is True


def test_list_etfs_ok():
    r = client.get("/etfs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_etfs_with_filters_ok():
    r = client.get("/etfs", params={"pre_listing_only": True, "country": "KR"})
    assert r.status_code == 200


def test_list_countries_ok():
    r = client.get("/countries")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_holdings_stats_ok():
    r = client.get("/stats/holdings")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_stats_summary_ok():
    r = client.get("/stats/summary")
    assert r.status_code == 200
    assert "total_etfs" in r.json()


def test_etf_detail_and_holdings_ok():
    """Pick a real etf_key from the list, then exercise detail + holdings."""
    listing = client.get("/etfs").json()
    if not listing:
        return  # empty DB — nothing to drill into
    etf_key = listing[0]["etf_key"]

    detail = client.get(f"/etfs/{etf_key}")
    assert detail.status_code == 200

    holdings = client.get(f"/etfs/{etf_key}/holdings")
    assert holdings.status_code == 200
    assert isinstance(holdings.json(), list)


def test_etf_detail_404_on_missing():
    r = client.get("/etfs/__nope__")
    assert r.status_code == 404


def test_stats_with_etf_keys_branch():
    """Exercise the etf_keys branch (the _etf_key_params helper path)."""
    listing = client.get("/etfs").json()
    if not listing:
        return
    keys = [row["etf_key"] for row in listing[:2]]
    r = client.get("/stats/holdings", params=[("etf_keys", k) for k in keys])
    assert r.status_code == 200
    r2 = client.get("/stats/summary", params=[("etf_keys", k) for k in keys])
    assert r2.status_code == 200
