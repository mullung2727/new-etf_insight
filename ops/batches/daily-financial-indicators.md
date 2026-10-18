# daily-financial-indicators

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

Daily 19:00; DART periodic-report financial indicators and key accounts into
`<데이터루트>/db/financial_indicators.sqlite3` (<데이터루트> = .env NEW_ETF_DATA_ROOT, 현재 E:\new-etf_insight_data — data_paths.db_dir()) via
`ops/scheduled-tasks/run-financial-indicators.ps1`. Discord report only on days
with filings.
