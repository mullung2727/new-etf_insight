"""날짜 기준 유튜브 영상 LangGraph 분석 루프 (그래프 바깥).

Usage (from etl/):
    uv run python scripts/run_youtube_analysis.py --date 2026-07-09
    uv run python scripts/run_youtube_analysis.py --date 2026-07-09 --force
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.collect_youtube import DEFAULT_DB, ensure_schema as ensure_videos_schema
    from scripts.youtube_langgraph.youtube_analysis_langgraph import run_video
except ImportError:
    from collect_youtube import DEFAULT_DB, ensure_schema as ensure_videos_schema
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from youtube_langgraph.youtube_analysis_langgraph import run_video


def list_videos_for_date(con: sqlite3.Connection, date_kst: str) -> list[tuple[str, str]]:
    rows = con.execute(
        """
        SELECT channel_id, video_id FROM youtube_videos
        WHERE date_kst=?
          AND transcript IS NOT NULL AND TRIM(transcript) != ''
        ORDER BY channel_id, video_id
        """,
        (date_kst,),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="KST YYYY-MM-DD")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[run_youtube_analysis] ERROR db not found: {db_path}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db_path))
    try:
        ensure_videos_schema(con)
        videos = list_videos_for_date(con, args.date)
    finally:
        con.close()

    ok = skipped = errors = 0
    for channel_id, video_id in videos:
        try:
            result = run_video(
                channel_id=channel_id,
                video_id=video_id,
                db_path=str(db_path),
                force=args.force,
            )
            if result.get("skip"):
                skipped += 1
            elif result.get("persisted"):
                ok += 1
            else:
                errors += 1
            print(
                f"[run_youtube_analysis] {channel_id}/{video_id} "
                f"skip={result.get('skip')} llm={result.get('llm_calls')} "
                f"persisted={result.get('persisted')}"
            )
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(
                f"[run_youtube_analysis] ERROR {channel_id}/{video_id}: {exc}",
                file=sys.stderr,
            )

    print(
        f"[run_youtube_analysis] date={args.date} videos={len(videos)} "
        f"ok={ok} skipped={skipped} errors={errors}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
