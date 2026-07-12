"""youtube pending/collect helpers (no live network)."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

import duck_watchlist
import youtube_ops


@pytest.fixture
def temp_db(monkeypatch):
    d = Path(tempfile.mkdtemp())
    db = d / "youtube_public.sqlite3"
    con = sqlite3.connect(str(db))
    con.executescript(
        """
        CREATE TABLE youtube_videos (
            channel_id TEXT, video_id TEXT, title TEXT,
            published_at_utc TEXT, date_kst TEXT, url TEXT,
            transcript TEXT, transcript_lang TEXT, transcript_source TEXT,
            raw_json TEXT, created_at TEXT, updated_at TEXT,
            UNIQUE(channel_id, video_id)
        );
        CREATE TABLE youtube_video_summaries (
            channel_id TEXT, video_id TEXT, date_kst TEXT,
            model TEXT, summary_json TEXT,
            created_at TEXT, updated_at TEXT,
            UNIQUE(channel_id, video_id)
        );
        """
    )
    con.execute(
        "INSERT INTO youtube_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "UCe2etestChannelId_zzzz1",
            "AAAAAAAAAAA",
            "pending vid",
            "2026-07-09T00:00:00+00:00",
            "2026-07-09",
            "https://www.youtube.com/watch?v=AAAAAAAAAAA",
            "hello transcript",
            "ko",
            "auto",
            "{}",
            "t0",
            "t0",
        ),
    )
    con.execute(
        "INSERT INTO youtube_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "UCe2etestChannelId_zzzz1",
            "BBBBBBBBBBB",
            "done vid",
            "2026-07-09T00:00:00+00:00",
            "2026-07-09",
            "https://www.youtube.com/watch?v=BBBBBBBBBBB",
            "done body",
            "ko",
            "auto",
            "{}",
            "t0",
            "t0",
        ),
    )
    con.execute(
        "INSERT INTO youtube_video_summaries VALUES (?,?,?,?,?,?,?)",
        (
            "UCe2etestChannelId_zzzz1",
            "BBBBBBBBBBB",
            "2026-07-09",
            "codex",
            json.dumps({"headline": "h", "issues": [], "bullets": [], "risk_or_caveat": None}),
            "t0",
            "t0",
        ),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(duck_watchlist, "YOUTUBE_DB_PATH", db)
    monkeypatch.setattr(youtube_ops, "YOUTUBE_DB_PATH", db)
    yield db
    import shutil

    shutil.rmtree(d, ignore_errors=True)


def test_list_pending_excludes_summarized(temp_db):
    rows = youtube_ops.list_pending(from_date="2026-07-01", to_date="2026-07-31")
    ids = {r["video_id"] for r in rows}
    assert "AAAAAAAAAAA" in ids
    assert "BBBBBBBBBBB" not in ids
    assert rows[0]["has_transcript"] is True
    assert rows[0]["status"] == "ready"


def test_list_pending_includes_no_transcript(temp_db):
    con = sqlite3.connect(str(temp_db))
    con.execute(
        "INSERT INTO youtube_videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "UCe2etestChannelId_zzzz1",
            "CCCCCCCCCCC",
            "no sub",
            "2026-07-09T00:00:00+00:00",
            "2026-07-09",
            "https://www.youtube.com/watch?v=CCCCCCCCCCC",
            None,
            None,
            None,
            "{}",
            "t0",
            "t0",
        ),
    )
    con.commit()
    con.close()
    rows = youtube_ops.list_pending(from_date="2026-07-01", to_date="2026-07-31")
    no_sub = next(r for r in rows if r["video_id"] == "CCCCCCCCCCC")
    assert no_sub["status"] == "no_transcript"
    assert no_sub["has_transcript"] is False
