"""YouTube 읽기 API 테스트.

Run from api/:
    uv run --with pytest --with httpx pytest test_youtube_api.py
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import duck_watchlist
from main import app

client = TestClient(app)


@pytest.fixture
def temp_db(monkeypatch):
    d = Path(tempfile.mkdtemp())
    db = d / "youtube_public.sqlite3"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE youtube_stock_insights (
            date_kst TEXT, ticker TEXT, name TEXT,
            mention_channels TEXT, source_video_ids TEXT,
            discovery_reason TEXT, analysis TEXT,
            created_at TEXT, updated_at TEXT,
            UNIQUE(date_kst, ticker)
        );
        CREATE TABLE youtube_video_summaries (
            channel_id TEXT, video_id TEXT, date_kst TEXT,
            model TEXT, summary_json TEXT,
            created_at TEXT, updated_at TEXT,
            UNIQUE(channel_id, video_id)
        );
        CREATE TABLE youtube_videos (
            channel_id TEXT, video_id TEXT, title TEXT,
            published_at_utc TEXT, date_kst TEXT, url TEXT,
            transcript TEXT, transcript_lang TEXT, transcript_source TEXT,
            raw_json TEXT, created_at TEXT, updated_at TEXT,
            UNIQUE(channel_id, video_id)
        );
        """
    )
    con.execute(
        "INSERT INTO youtube_stock_insights VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "2026-07-08",
            "017670",
            "SK텔레콤",
            json.dumps(["UCeN2YeJcBCRJoXgzF_OU3qw"]),
            json.dumps(["o-u9WgPBm4g"]),
            "15GW 설계 주체",
            "AI 인프라 중심",
            "t0",
            "t0",
        ),
    )
    con.execute(
        "INSERT INTO youtube_stock_insights VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "2026-07-07",
            "017670",
            "SK텔레콤",
            json.dumps(["UCother"]),
            json.dumps(["vidOld"]),
            "이전 언급",
            None,
            "t0",
            "t0",
        ),
    )
    con.execute(
        "INSERT INTO youtube_stock_insights VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "2026-07-08",
            "000660",
            "SK하이닉스",
            json.dumps(["UCeN2YeJcBCRJoXgzF_OU3qw"]),
            json.dumps(["o-u9WgPBm4g"]),
            "HBM",
            "메모리",
            "t0",
            "t0",
        ),
    )
    summary = {
        "headline": "한 줄",
        "issues": [{"title": "이슈", "summary": "설명", "time_hint": None}],
        "bullets": ["b1"],
        "risk_or_caveat": None,
    }
    con.execute(
        "INSERT INTO youtube_video_summaries VALUES (?,?,?,?,?,?,?)",
        (
            "UCeN2YeJcBCRJoXgzF_OU3qw",
            "o-u9WgPBm4g",
            "2026-07-08",
            "codex",
            json.dumps(summary, ensure_ascii=False),
            "t0",
            "t0",
        ),
    )
    con.execute(
        "INSERT INTO youtube_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "UCeN2YeJcBCRJoXgzF_OU3qw",
            "o-u9WgPBm4g",
            "AI 데이터센터",
            "2026-07-08T10:00:00+00:00",
            "2026-07-08",
            "https://www.youtube.com/watch?v=o-u9WgPBm4g",
            "t",
            "ko",
            "auto",
            "{}",
            "t0",
            "t0",
        ),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(duck_watchlist, "YOUTUBE_DB_PATH", db)
    yield
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def test_mentions_ticker_newest_first(temp_db):
    body = client.get("/youtube/mentions/017670").json()
    assert len(body) == 2
    assert body[0]["date_kst"] == "2026-07-08"
    assert body[0]["video_ids"] == ["o-u9WgPBm4g"]
    assert body[0]["analysis"] == "AI 인프라 중심"
    assert body[1]["date_kst"] == "2026-07-07"


def test_mentions_from_filter(temp_db):
    body = client.get("/youtube/mentions/017670", params={"from": "2026-07-08"}).json()
    assert len(body) == 1
    assert body[0]["date_kst"] == "2026-07-08"


def test_mentions_other_ticker(temp_db):
    body = client.get("/youtube/mentions/000660").json()
    assert len(body) == 1
    assert body[0]["name"] == "SK하이닉스"


def test_summaries_by_date(temp_db):
    body = client.get("/youtube/summaries", params={"date": "2026-07-08"}).json()
    assert len(body) == 1
    s = body[0]
    assert s["video_id"] == "o-u9WgPBm4g"
    assert s["title"] == "AI 데이터센터"
    assert s["headline"] == "한 줄"
    assert s["bullets"] == ["b1"]
    assert "watch?v=" in s["url"]
    assert "duration_sec" not in s
    assert "transcript_chars" not in s
    # fixture: o-u9WgPBm4g → SK텔레콤 + SK하이닉스
    tickers = {x["ticker"] for x in s.get("stocks") or []}
    assert "017670" in tickers
    assert "000660" in tickers


def test_summaries_all_no_date(temp_db):
    body = client.get("/youtube/summaries").json()
    assert len(body) == 1
    assert body[0]["video_id"] == "o-u9WgPBm4g"
    assert body[0]["channel_label"]  # resolved or channel_id fallback
    assert isinstance(body[0].get("stocks"), list)


def test_summaries_from_to_range(temp_db):
    body = client.get(
        "/youtube/summaries", params={"from": "2026-07-08", "to": "2026-07-08"}
    ).json()
    assert len(body) == 1
    empty = client.get(
        "/youtube/summaries", params={"from": "2026-07-01", "to": "2026-07-07"}
    ).json()
    assert empty == []


def test_summaries_empty_date(temp_db):
    body = client.get("/youtube/summaries", params={"date": "2099-01-01"}).json()
    assert body == []


def test_collect_url_ok(temp_db, monkeypatch):
    import youtube_ops

    def fake_run(*, url: str):
        assert "jdZls" in url or url
        return {
            "video_id": "jdZls-7iVps",
            "channel_id": "UCe2etestChannelId_zzzz1",
            "date_kst": "2026-07-12",
            "title": "t",
            "status": "inserted",
            "has_transcript": True,
            "url": "https://www.youtube.com/watch?v=jdZls-7iVps",
        }

    monkeypatch.setattr(youtube_ops, "run_collect_url", fake_run)
    # router imports run_collect_url at module level — patch routers.youtube too
    import routers.youtube as yr

    monkeypatch.setattr(yr, "run_collect_url", fake_run)
    r = client.post(
        "/youtube/collect-url",
        json={"url": "https://www.youtube.com/watch?v=jdZls-7iVps"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "inserted"
    assert body["video_id"] == "jdZls-7iVps"
    assert body["date_kst"] == "2026-07-12"


def test_collect_url_bad_url(temp_db, monkeypatch):
    import routers.youtube as yr

    def boom(*, url: str):
        raise ValueError("채널 주소가 아니라 영상 URL을 입력하세요.")

    monkeypatch.setattr(yr, "run_collect_url", boom)
    r = client.post(
        "/youtube/collect-url",
        json={"url": "https://www.youtube.com/@someone"},
    )
    assert r.status_code == 400
    assert "채널" in r.json()["detail"]


def test_pending_date_filter(temp_db):
    # seed: 요약 없는 영상 1건 (summaries에 없는 별도 video)
    import duck_watchlist

    db = Path(duck_watchlist.YOUTUBE_DB_PATH)
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO youtube_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "UCe2etestChannelId_zzzz1",
            "PENDINGVIDEO",
            "대기영상",
            "2026-07-10T00:00:00+00:00",
            "2026-07-10",
            "https://www.youtube.com/watch?v=PENDINGVIDEO",
            "transcript body",
            "ko",
            "auto",
            "{}",
            "t0",
            "t0",
        ),
    )
    con.commit()
    con.close()
    in_range = client.get(
        "/youtube/pending", params={"from": "2026-07-10", "to": "2026-07-10"}
    ).json()
    assert any(x["video_id"] == "PENDINGVIDEO" for x in in_range)
    out = client.get(
        "/youtube/pending", params={"from": "2026-07-01", "to": "2026-07-09"}
    ).json()
    assert not any(x["video_id"] == "PENDINGVIDEO" for x in out)
