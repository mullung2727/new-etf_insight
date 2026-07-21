"""날짜 기준 유튜브 영상 LangGraph 분석 루프 (그래프 바깥).

Usage (from etl/):
    uv run python scripts/run_youtube_analysis.py --date 2026-07-09
    uv run python scripts/run_youtube_analysis.py --date 2026-07-09 --auto-only
    uv run python scripts/run_youtube_analysis.py --date 2026-07-09 --auto-only --lookback-days 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.collect_youtube import DEFAULT_DB, ensure_schema as ensure_videos_schema
    from scripts.youtube_channels import load_auto_summary_channels
    from scripts.youtube_langgraph.youtube_analysis_langgraph import run_video
except ImportError:
    from collect_youtube import DEFAULT_DB, ensure_schema as ensure_videos_schema
    from youtube_channels import load_auto_summary_channels

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from youtube_langgraph.youtube_analysis_langgraph import run_video


"""자동 요약 대상 대본 길이 범위. 하한은 2분 이내 쇼츠·티저 배제용.

실측: 5분짜리 속보 코너가 2,925자 → 3,000은 과하게 잘림. 2,000이면 쇼츠만 걸림.
"""
MIN_TRANSCRIPT_CHARS = 2000
MAX_TRANSCRIPT_CHARS = 35000


def _date_from_iso(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def list_videos(
    con: sqlite3.Connection,
    *,
    date_from: str,
    date_to: str,
    channel_ids: list[str] | None = None,
    pending_only: bool = True,
    require_transcript: bool = True,
) -> list[tuple[str, str]]:
    """date_kst 범위 영상. pending_only면 요약 없는 것만.

    channel_ids 가 빈 리스트면 0건. None이면 채널 필터 없음.
    require_transcript=False면 대본 없는 영상도 포함(STT 폴백 대상).
    """
    if channel_ids is not None and len(channel_ids) == 0:
        return []

    clauses = [
        "v.date_kst >= ?",
        "v.date_kst <= ?",
    ]
    if require_transcript:
        clauses += [
            "v.transcript IS NOT NULL",
            "TRIM(v.transcript) != ''",
            "length(v.transcript) BETWEEN ? AND ?",
        ]
    args: list[object] = [date_from, date_to]
    if require_transcript:
        args += [MIN_TRANSCRIPT_CHARS, MAX_TRANSCRIPT_CHARS]

    tables = {
        r[0]
        for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    join = ""
    if pending_only and "youtube_video_summaries" in tables:
        join = (
            "LEFT JOIN youtube_video_summaries s "
            "ON s.channel_id=v.channel_id AND s.video_id=v.video_id"
        )
        clauses.append("s.video_id IS NULL")

    if channel_ids is not None:
        ph = ",".join("?" * len(channel_ids))
        clauses.append(f"v.channel_id IN ({ph})")
        args.extend(channel_ids)

    sql = f"""
        SELECT v.channel_id, v.video_id FROM youtube_videos v
        {join}
        WHERE {" AND ".join(clauses)}
        ORDER BY v.date_kst, v.channel_id, v.video_id
    """
    rows = con.execute(sql, args).fetchall()
    return [(r[0], r[1]) for r in rows]


def list_videos_for_date(con: sqlite3.Connection, date_kst: str) -> list[tuple[str, str]]:
    """하위호환: 당일·대본 있는 전부(이미 요약된 것도 포함 — force 경로용)."""
    return list_videos(
        con,
        date_from=date_kst,
        date_to=date_kst,
        channel_ids=None,
        pending_only=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="KST YYYY-MM-DD (범위 끝)")
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=0,
        help="date 포함 N일 더 과거까지 (0=당일만). auto 재시도용",
    )
    ap.add_argument(
        "--auto-only",
        action="store_true",
        help="summary_mode=auto 채널 + 미요약만",
    )
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--no-stt",
        action="store_true",
        help="대본 없을 때 Gemini STT 폴백 끔(기본 켜짐)",
    )
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # GEMINI_API_KEY (STT)

    stt_enabled = not args.no_stt

    if args.lookback_days < 0:
        print("[run_youtube_analysis] ERROR lookback-days < 0", file=sys.stderr)
        return 1

    end = _date_from_iso(args.date)
    start = end - dt.timedelta(days=args.lookback_days)
    date_from = start.isoformat()
    date_to = end.isoformat()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[run_youtube_analysis] ERROR db not found: {db_path}", file=sys.stderr)
        return 1

    auto_ids: list[str] | None = None
    if args.auto_only:
        auto_ids = list(load_auto_summary_channels().keys())
        print(
            f"[run_youtube_analysis] auto-only channels={len(auto_ids)} "
            f"{auto_ids}"
        )

    con = sqlite3.connect(str(db_path))
    try:
        ensure_videos_schema(con)
        # auto: 미요약만. force 없이 일반 날짜 루프는 예전처럼 대본 있는 전부
        # (run_video 내부 has_summary 스킵). auto-only는 pending 필터로 비용 절감.
        pending_only = bool(args.auto_only) and not args.force
        videos = list_videos(
            con,
            date_from=date_from,
            date_to=date_to,
            channel_ids=auto_ids,
            pending_only=pending_only,
            require_transcript=not stt_enabled,
        )
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
                stt_enabled=stt_enabled,
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
        f"[run_youtube_analysis] from={date_from} to={date_to} "
        f"auto_only={args.auto_only} videos={len(videos)} "
        f"ok={ok} skipped={skipped} errors={errors}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
