# YouTube Channel Collection Guide

Use this skill when collecting YouTube channel uploads + transcripts into
`etl/db/youtube_public.sqlite3`.

## Scope

- Collection only. Pipeline:
  1. channel_id (`UC…`) from `youtube_channels.json`
  2. public RSS upload list (`feeds/videos.xml?channel_id=…`)
  3. `youtube-transcript-api` plain-text transcript (manual/auto captions; **not STT**)
- LLM summary / stock extract is **separate**:  
  `uv run python scripts/run_youtube_analysis.py --date YYYY-MM-DD`  
  (LangGraph: chunk → reduce issues → stock; see `docs/youtube_tech.md` §5)
- No Discord in collect skill.
- No YouTube Data API key, no yt-dlp in this phase.
- Channel list single source: `etl/scripts/youtube_channels.json`.

## Core Rules

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Prefer `uv run python`.
- `--date` is KST `YYYY-MM-DD`. Filter by published→KST date, never raw UTC date alone.
- Idempotent: `UNIQUE(channel_id, video_id)`. Re-run updates rows, no duplicates.
- Missing captions → `transcript=NULL`, channel still succeeds (`skipped_no_transcript`).
- RSS only returns recent ~15 entries. Date outside window → 0 rows is normal.
- Shorts = normal video (same video_id / RSS / transcript path).

## Collect one date, all registered channels

```bash
uv run python scripts/run_youtube_channels.py --date 2026-07-09
```

## Collect one date, single channel

```bash
uv run python scripts/run_youtube_channels.py --date 2026-07-09 --channel UCeN2YeJcBCRJoXgzF_OU3qw
```

## Single-channel script

```bash
uv run python scripts/collect_youtube.py --channel-id UCeN2YeJcBCRJoXgzF_OU3qw --date 2026-07-09
```

## Notes

- One channel failure does not stop others; process exit 1 if any channel failed.
- Console Korean on Windows: stdout reconfigured utf-8 in scripts.
- Admin UI: `/admin/settings` → 유튜브 탭 manages `youtube_channels.json`.

## Verify collected rows

```bash
uv run python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); import sqlite3; con=sqlite3.connect('db/youtube_public.sqlite3'); [print(r) for r in con.execute(\"SELECT date_kst, channel_id, COUNT(*), SUM(transcript IS NOT NULL) FROM youtube_videos GROUP BY date_kst, channel_id ORDER BY date_kst, channel_id\")]"
```
