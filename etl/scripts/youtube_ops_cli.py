"""api 서브프로세스용 YouTube 수집/요약 CLI. stdin JSON → stdout JSON.

Usage (from etl/):
  echo {"op":"collect",...} | uv run python scripts/youtube_ops_cli.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def op_collect(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from scripts.collect_youtube import collect_channel_range, ensure_schema
    except ImportError:
        from collect_youtube import collect_channel_range, ensure_schema

    from_date = payload["from_date"]
    to_date = payload["to_date"]
    channel_ids = payload.get("channel_ids") or []
    db = payload.get("db")
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    results: list[dict] = []
    errors: list[dict] = []
    try:
        ensure_schema(con)
        for cid in channel_ids:
            try:
                stats = collect_channel_range(con, cid, from_date, to_date)
                con.commit()
                results.append(stats)
            except Exception as exc:  # noqa: BLE001
                errors.append({"channel_id": cid, "error": str(exc)})
    finally:
        con.close()
    return {
        "from": from_date,
        "to": to_date,
        "channels": len(channel_ids),
        "results": results,
        "errors": errors,
    }


def op_collect_selected(payload: dict[str, Any]) -> dict[str, Any]:
    """선택 video 목록만 자막 수집 (STT 없음)."""
    try:
        from scripts.collect_youtube import collect_videos, ensure_schema
    except ImportError:
        from collect_youtube import collect_videos, ensure_schema

    videos = payload.get("videos") or []
    db = payload.get("db")
    if not videos:
        return {
            "targets": 0,
            "matched": 0,
            "inserted": 0,
            "updated": 0,
            "skipped_no_transcript": 0,
            "skipped_bad": 0,
            "error": "videos required",
        }
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(con)
        stats = collect_videos(con, videos)
        con.commit()
    finally:
        con.close()
    return stats


def op_collect_url(payload: dict[str, Any]) -> dict[str, Any]:
    """영상 URL 1건 → 메타+대본 upsert."""
    try:
        from scripts.collect_youtube import collect_url, ensure_schema
    except ImportError:
        from collect_youtube import collect_url, ensure_schema

    url = (payload.get("url") or "").strip()
    db = payload.get("db")
    if not url:
        raise ValueError("영상 URL을 입력하세요.")
    con = sqlite3.connect(str(db))
    con.execute("PRAGMA journal_mode=WAL")
    try:
        ensure_schema(con)
        result = collect_url(con, url)
        con.commit()
    finally:
        con.close()
    return result


def op_list_catalog(payload: dict[str, Any]) -> dict[str, Any]:
    """선택 채널 RSS 영상 목록 + duration (대본/STT 없음)."""
    try:
        from scripts.collect_youtube import list_channel_catalog
    except ImportError:
        from collect_youtube import list_channel_catalog

    channel_ids = payload.get("channel_ids") or []
    with_duration = payload.get("with_duration", True)
    videos: list[dict] = []
    errors: list[dict] = []
    for cid in channel_ids:
        try:
            rows = list_channel_catalog(cid, with_duration=bool(with_duration))
            videos.extend(rows)
        except Exception as exc:  # noqa: BLE001
            errors.append({"channel_id": cid, "error": str(exc)})
    # 최신 published 우선 (ISO 문자열 내림차순)
    videos.sort(key=lambda r: r.get("published_at_utc") or "", reverse=True)
    return {
        "channels": len(channel_ids),
        "count": len(videos),
        "videos": videos,
        "errors": errors,
    }


def op_summarize(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        from scripts.youtube_langgraph.youtube_analysis_langgraph import run_video
    except ImportError:
        from youtube_langgraph.youtube_analysis_langgraph import run_video

    db = str(payload.get("db"))
    force = bool(payload.get("force"))
    video_ids = payload.get("video_ids")
    from_date = payload.get("from_date")
    to_date = payload.get("to_date")
    channel_ids = payload.get("channel_ids")

    con = sqlite3.connect(db)
    targets: list[tuple[str, str]] = []
    try:
        if video_ids:
            for vid in video_ids:
                row = con.execute(
                    "SELECT channel_id FROM youtube_videos WHERE video_id=? LIMIT 1",
                    (vid,),
                ).fetchone()
                if row:
                    targets.append((row[0], vid))
        else:
            # pending
            clauses = [
                "v.transcript IS NOT NULL",
                "TRIM(v.transcript) != ''",
            ]
            args: list[Any] = []
            if from_date:
                clauses.append("v.date_kst>=?")
                args.append(from_date)
            if to_date:
                clauses.append("v.date_kst<=?")
                args.append(to_date)
            if channel_ids:
                ph = ",".join("?" * len(channel_ids))
                clauses.append(f"v.channel_id IN ({ph})")
                args.extend(channel_ids)
            try:
                from scripts.run_youtube_analysis import (
                    MAX_TRANSCRIPT_CHARS,
                    MIN_TRANSCRIPT_CHARS,
                )
            except ImportError:
                from run_youtube_analysis import (
                    MAX_TRANSCRIPT_CHARS,
                    MIN_TRANSCRIPT_CHARS,
                )
            clauses.append("length(v.transcript) BETWEEN ? AND ?")
            args.extend([MIN_TRANSCRIPT_CHARS, MAX_TRANSCRIPT_CHARS])

            tables = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "youtube_video_summaries" in tables and not force:
                join = (
                    "LEFT JOIN youtube_video_summaries s "
                    "ON s.channel_id=v.channel_id AND s.video_id=v.video_id"
                )
                extra = "AND s.video_id IS NULL"
            else:
                join = ""
                extra = ""

            rows = con.execute(
                f"""
                SELECT v.channel_id, v.video_id FROM youtube_videos v
                {join}
                WHERE {" AND ".join(clauses)} {extra}
                ORDER BY v.date_kst, v.video_id
                """,
                args,
            ).fetchall()
            targets = [(r[0], r[1]) for r in rows]
    finally:
        con.close()

    ok = skipped = 0
    errors: list[dict] = []
    for cid, vid in targets:
        try:
            result = run_video(
                channel_id=cid, video_id=vid, db_path=db, force=force
            )
            if result.get("skip"):
                skipped += 1
            elif result.get("persisted"):
                ok += 1
            else:
                errors.append(
                    {
                        "channel_id": cid,
                        "video_id": vid,
                        "error": "not_persisted",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                {"channel_id": cid, "video_id": vid, "error": str(exc)}
            )

    return {
        "targets": len(targets),
        "ok": ok,
        "skipped": skipped,
        "errors": errors,
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        op = payload.get("op")
        if op == "collect":
            _emit(op_collect(payload))
            return 0
        if op == "collect_selected":
            _emit(op_collect_selected(payload))
            return 0
        if op == "collect_url":
            _emit(op_collect_url(payload))
            return 0
        if op == "list_catalog":
            _emit(op_list_catalog(payload))
            return 0
        if op == "summarize":
            _emit(op_summarize(payload))
            return 0
        _emit({"error": f"unknown op: {op}"})
        return 1
    except Exception as exc:  # noqa: BLE001
        _emit({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
