# us-early

## Schedule

Schedule, timezone, Windows Task binding, legacy OpenClaw cron metadata, and
delivery live in `ops/batches/openclaw-cron.registry.json` (source of truth).
Do not duplicate them here.

## File Reading

Read this instruction file with a platform-neutral UTF-8-safe reader, such as
OpenClaw `file_fetch` or the agent's native workspace file access. Do not read
this file with host-default shell decoding, especially Windows PowerShell 5.1
`Get-Content` without an explicit UTF-8 setting.

## Purpose

06:30 KST early load of the previous US session for signal use, ahead of the
11:00 Yahoo batch (`us-daily`).

- Fetches 9 signal ETFs plus optional extra tickers (one per line, `#`
  comments; path via `US_EARLY_EXTRA_TICKERS`) from Kiwoom usa06012 through
  broker :8001 `/us/daily`, upserting into `us_ohlcv.duckdb` (`etf_ohlcv` for
  ETFs, `ohlcv` for extra tickers).
- Fetches VIX/VIX3M from the official CBOE history CSVs into
  `us_macro.duckdb` `index_ohlcv` (`^VIX`, `^VIX3M`).
- No Korean trading-day guard: US holidays exit quietly, judged on the
  broker's latest date, not the calendar.
- Exit 1 with one Discord failure notice when any target misses the broker's
  latest (QQQ) date; the 11:00 batch backfills and overwrites when healthy.

## Guardrails

- Safe to rerun the same day (upsert; stored rows change only on value diff).
- Do not hand-edit the loaded bars; rerun the script or wait for `us-daily`.
- Strategy names and rules stay out of this file.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\us-early`.
2. Today's log: `logs\us-early-YYYYMMDD.log`.
3. Latest dates per ticker in `db\us_ohlcv.duckdb` (`etf_ohlcv`, `ohlcv`) and
   `db\us_macro.duckdb` (`index_ohlcv` for `^VIX`/`^VIX3M`).
