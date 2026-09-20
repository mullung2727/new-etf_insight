# rebound-verify

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

16:00 confirmation for the private rebound strategy on the existing account
broker (`http://localhost:8001`). Reconciles sell fills, records the broker
net realized P/L per ticker, marks unsold positions as carried, and posts the
daily summary. It warns in Discord when the strategy's cumulative P/L reaches
-20% of the reference amount; it never stops the strategy by itself.

No orders are placed.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\rebound-verify`.
2. Today's log: `logs\rebound-verify-YYYYMMDD.log`.
3. `rebound_positions.realized` filled for today's closed rows.
