"""YouTube STT 폴백 — 자막 없는 영상 오디오를 Gemini로 전사해 대본을 채운다.

`youtube-transcript-api` 실패로 `transcript IS NULL`인 youtube_videos 행을
yt-dlp(오디오) → Gemini 2.5 Flash(전사)로 backfill 한다. 수집 hot-path는
건드리지 않음(비용/시간 안전). 온디맨드 opt-in.

의존: yt-dlp, google-genai, ffmpeg(mp3 변환).
키: 환경변수 GEMINI_API_KEY (Google AI Studio 무료티어).
스펙: docs/youtube_tech.md §4 (STT 폴백 — 2026-07-14 범위 확장).

Usage (from etl/):
  uv run python scripts/youtube_stt.py --limit 5
  uv run python scripts/youtube_stt.py --date-from 2026-07-01 --date-to 2026-07-14
  uv run python scripts/youtube_stt.py --channel UCxxx --limit 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from scripts.collect_youtube import DEFAULT_DB, ensure_schema
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from collect_youtube import DEFAULT_DB, ensure_schema

GEMINI_MODEL = "gemini-flash-latest"
STT_SOURCE = f"stt:{GEMINI_MODEL}"
TRANSCRIBE_PROMPT = (
    "이 오디오는 한국어 유튜브 영상입니다. 말한 내용을 정확히 한국어로 전사하세요. "
    "화자 표시·타임코드·설명·요약 없이 순수 대본 텍스트만 출력하세요."
)


def download_audio(video_id: str, out_dir: str | Path, *, run=subprocess.run) -> Path:
    """yt-dlp로 bestaudio → mp3. 실패 시 RuntimeError."""
    out_dir = Path(out_dir)
    out = out_dir / f"{video_id}.mp3"
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format",
        "mp3",
        "--audio-quality",
        "5",
        "--no-playlist",
        "-o",
        str(out_dir / f"{video_id}.%(ext)s"),
        f"https://www.youtube.com/watch?v={video_id}",
    ]
    r = run(cmd, capture_output=True, text=True)
    if getattr(r, "returncode", 1) != 0 or not out.exists():
        err = (getattr(r, "stderr", "") or "")[-300:]
        raise RuntimeError(f"yt-dlp 실패 {video_id}: {err}")
    return out


def transcribe_gemini(
    audio_path: str | Path,
    *,
    api_key: str | None = None,
    model: str = GEMINI_MODEL,
    client=None,
) -> str:
    """오디오 파일 → Gemini 전사 텍스트. 키 없으면 RuntimeError."""
    if client is None:
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY 환경변수가 없습니다.")
        from google import genai

        client = genai.Client(api_key=key)
    uploaded = client.files.upload(file=str(audio_path))
    resp = client.models.generate_content(
        model=model, contents=[TRANSCRIBE_PROMPT, uploaded]
    )
    return (getattr(resp, "text", "") or "").strip()


def fetch_transcript_stt(
    video_id: str,
    *,
    tmp_dir: str | Path | None = None,
    download_fn: Callable[..., Path] | None = None,
    transcribe_fn: Callable[..., str] | None = None,
) -> tuple[str | None, str | None, str | None]:
    """(text, lang, source). 실패/빈 결과면 (None, None, None).

    download_fn / transcribe_fn 주입으로 테스트 mock(네트워크 없음).
    """
    dl = download_fn or download_audio
    tr = transcribe_fn or transcribe_gemini

    def _run(work: Path) -> tuple[str | None, str | None, str | None]:
        audio = dl(video_id, work)
        text = (tr(audio) or "").strip()
        if not text:
            return (None, None, None)
        return (text, "ko", STT_SOURCE)

    if tmp_dir is not None:
        return _run(Path(tmp_dir))
    with tempfile.TemporaryDirectory(prefix="ytstt_") as work:
        return _run(Path(work))


def list_missing(
    con: sqlite3.Connection,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    channel_id: str | None = None,
    limit: int = 5,
) -> list[tuple[str, str]]:
    """transcript IS NULL/빈 행 (channel_id, video_id). 최신 date_kst 우선."""
    clauses = ["(transcript IS NULL OR TRIM(transcript) = '')"]
    args: list[object] = []
    if date_from:
        clauses.append("date_kst >= ?")
        args.append(date_from)
    if date_to:
        clauses.append("date_kst <= ?")
        args.append(date_to)
    if channel_id:
        clauses.append("channel_id = ?")
        args.append(channel_id)
    sql = (
        "SELECT channel_id, video_id FROM youtube_videos WHERE "
        + " AND ".join(clauses)
        + " ORDER BY date_kst DESC, video_id LIMIT ?"
    )
    args.append(limit)
    return [(r[0], r[1]) for r in con.execute(sql, args).fetchall()]


def update_transcript(
    con: sqlite3.Connection,
    *,
    channel_id: str,
    video_id: str,
    text: str,
    lang: str,
    source: str,
) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute(
        """
        UPDATE youtube_videos
        SET transcript = ?, transcript_lang = ?, transcript_source = ?, updated_at = ?
        WHERE channel_id = ? AND video_id = ?
        """,
        (text, lang, source, now, channel_id, video_id),
    )


def backfill(
    con: sqlite3.Connection,
    rows: list[tuple[str, str]],
    *,
    stt_fn: Callable[..., tuple[str | None, str | None, str | None]] | None = None,
) -> dict:
    """rows 각각 STT 전사 후 DB 갱신. stt_fn 주입으로 테스트 mock."""
    run_stt = stt_fn or fetch_transcript_stt
    filled = skipped = errors = 0
    for channel_id, video_id in rows:
        try:
            text, lang, source = run_stt(video_id)
        except Exception as exc:  # noqa: BLE001 — 한 건 실패가 배치 전체 죽이지 않음
            errors += 1
            print(f"[youtube_stt] ERROR {video_id}: {exc}", file=sys.stderr)
            continue
        if not text:
            skipped += 1
            print(f"[youtube_stt] skip(no_text) {video_id}")
            continue
        update_transcript(
            con,
            channel_id=channel_id,
            video_id=video_id,
            text=text,
            lang=lang or "ko",
            source=source or STT_SOURCE,
        )
        con.commit()
        filled += 1
        print(f"[youtube_stt] filled {video_id} chars={len(text)}")
    return {"filled": filled, "skipped": skipped, "errors": errors}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--date-from")
    ap.add_argument("--date-to")
    ap.add_argument("--channel")
    ap.add_argument("--limit", type=int, default=5, help="한 번에 처리할 최대 영상 수")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # GEMINI_API_KEY

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[youtube_stt] ERROR db not found: {db_path}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db_path))
    try:
        ensure_schema(con)
        rows = list_missing(
            con,
            date_from=args.date_from,
            date_to=args.date_to,
            channel_id=args.channel,
            limit=args.limit,
        )
        print(f"[youtube_stt] missing={len(rows)} (limit={args.limit})")
        stats = backfill(con, rows)
    finally:
        con.close()

    print(
        f"[youtube_stt] filled={stats['filled']} "
        f"skipped={stats['skipped']} errors={stats['errors']}"
    )
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
