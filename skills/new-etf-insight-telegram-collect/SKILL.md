# Telegram Public Channel Collection Guide

Use this skill when collecting Telegram public-channel posts into
`etl/db/telegram_public.sqlite3`.

## Scope

- Collection only. Reads `t.me/s/<channel>` public preview pages, filters by KST
  date, upserts raw posts. No summarization, no LLM. Summary/analysis is a
  separate future plan.
- Channel list is data-driven in `etl/scripts/telegram_channels.json` — this is
  the single source of truth. Add a channel = add one JSON entry, no code change.

## Core Rules

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Prefer `uv run python`. If `uv` missing but `.venv` exists, use
  `.\.venv\Scripts\python.exe`.
- `--date` is KST `YYYY-MM-DD`. Posts are filtered by KST date, never UTC.
- Idempotent: `UNIQUE(channel, post_id)`. Re-running the same date updates
  existing rows, never duplicates.
- SQLite output: `etl/db/telegram_public.sqlite3` (git-ignored).

## Collect one date, all registered channels

```bash
uv run python scripts/run_telegram_channels.py --date 2026-07-01
```

## Collect one date, single channel

```bash
uv run python scripts/run_telegram_channels.py --date 2026-07-01 --channel getfeed
```

## Collect a date range

Loop the dates (KST). PowerShell:

```powershell
foreach ($d in 1..6) {
  $date = "2026-07-{0:D2}" -f $d
  uv run python scripts/run_telegram_channels.py --date $date
}
```

## Notes

- t.me preview responses can time out on high-volume channels. On timeout,
  re-run that single channel with `--channel <name>` — collection is idempotent
  so retries are safe.
- Weekend/holiday: real-time disclosure bots (e.g. `awake_realtimeCheck`) and
  editorial channels (e.g. `butler_works`) may legitimately return 0 posts.
- Private / preview-blocked channels raise an error (empty first page). All
  currently-registered channels are public.
- Console cp949 crash on Windows when printing Korean: reconfigure stdout to
  utf-8 (`sys.stdout.reconfigure(encoding="utf-8")`) or dump to file and Read.

## Verify collected rows

```bash
uv run python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); import sqlite3; con=sqlite3.connect('db/telegram_public.sqlite3'); [print(r) for r in con.execute(\"SELECT date_kst, channel, COUNT(*) FROM telegram_posts GROUP BY date_kst, channel ORDER BY date_kst, channel\")]"
```
