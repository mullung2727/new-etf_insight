# high52-verify

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

16:00 fill confirmation for the private high52 strategy on its own account
broker (`http://localhost:8002`). It places no orders.

- Confirms today's buys from the broker's fill history (kt00007); unfilled
  limit buys become `expired`.
- Settles sell fills (`filled` / `partial`); a sell with no fill is released
  so the next day decides again.
- Closes positions no longer in the balance as `missing` (never when the
  balance lookup fails).
- Updates each position bought before today once per day from today's high.

It posts its own Discord summary.

## Guardrails

- Safe to rerun the same day (idempotent).
- Do not modify `high52_positions` manually.
- Strategy rules stay private (`research/private/`).

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\high52-verify`.
2. Today's log: `logs\high52-verify-YYYYMMDD.log`.
3. `high52_positions` rows in `db\watchlist.sqlite3`.
