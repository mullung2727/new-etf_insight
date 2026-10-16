# orderbook-archive

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

Mon-Fri 20:15; convert the finished daily orderbook sqlite (C:) to parquet on
D:, verify, then delete the C: copy.

No orders are placed.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\orderbook-archive`.
2. Today's log: `logs\orderbook-archive-YYYYMMDD.log`.
3. D: parquet present and verified; C: copy deleted.
