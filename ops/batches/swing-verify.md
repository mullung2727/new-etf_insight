# swing-verify

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

16:00 confirmation for the private swing strategy on the existing account
broker (`http://localhost:8001`). Reconciles sells filled in the closing
auction, records realized P/L, updates the budget reservation, and posts the
daily summary.

No orders are placed.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\swing-verify`.
2. Today's log: `logs\swing-verify-YYYYMMDD.log`.
3. `swing_trade_positions.realized` filled for today's closed rows.
