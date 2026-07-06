# Telegram Collected-Posts Query Guide

Use this skill when searching/reading Telegram posts already collected into
`etl/db/telegram_public.sqlite3` (see `new-etf-insight-telegram-collect` for
collection).

## Scope

- Read-only query over `telegram_posts`. Free-text keyword search + channel +
  KST date-range filters. No summarization, no writes (`PRAGMA query_only`).
- For dumping one channel+date to a JSON/MD file instead, use
  `export_telegram_public.py`.

## Core Rules

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Prefer `uv run python`; else `.\.venv\Scripts\python.exe`.
- Dates are KST `YYYY-MM-DD`.
- The script reconfigures stdout to utf-8, so Korean prints without the Windows
  cp949 crash.

## Keyword search across all channels, date range

```bash
uv run python scripts/query_telegram_public.py --keyword 삼성전자 --from 2026-07-01 --to 2026-07-06
```

## Filter by channel

```bash
uv run python scripts/query_telegram_public.py --channel getfeed --from 2026-07-01 --to 2026-07-06
```

## Full body (no snippet truncation) + limit

```bash
uv run python scripts/query_telegram_public.py --keyword 반도체 --full --limit 30
```

## Options

- `--keyword` : body substring match (`text LIKE '%kw%'`).
- `--channel` : single channel slug.
- `--from` / `--to` : KST date range (either or both optional).
- `--limit` : max rows (default 100).
- `--full` : print full body; default prints a 120-char single-line snippet.
- `--db` : override DB path (default `etl/db/telegram_public.sqlite3`).

Output line: `[date_kst] channel/post_id: <body>`, ending with a `--- N건 ---`
count.

## Ad-hoc SQL

For anything the CLI does not cover (aggregates, joins), query the SQLite
directly — reconfigure stdout to utf-8 first:

```bash
uv run python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); import sqlite3; con=sqlite3.connect('db/telegram_public.sqlite3'); con.execute('PRAGMA query_only=ON'); [print(r) for r in con.execute(\"SELECT channel, date_kst, COUNT(*) FROM telegram_posts GROUP BY channel, date_kst ORDER BY date_kst, channel\")]"
```

Schema: `telegram_posts(channel, post_id, post_ref, posted_at_utc, date_kst,
text, links_json, raw_json, created_at, updated_at)`, indexed on
`(channel, date_kst)`.
