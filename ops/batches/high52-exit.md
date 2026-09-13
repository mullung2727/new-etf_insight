# high52-exit

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

Intraday exit worker for the private high52 strategy on its own account
broker (`http://localhost:8002`). Starts 08:50, judges from 09:00 (never on
pre-open quotes), and stops at 15:18:30 when the order batch takes over the
account. Sells with market orders and posts one Discord line per sell.

Positions bought today are not watched until the 16:00 verify batch confirms
them.

## Guardrails

- One worker per day (task `MultipleInstancesPolicy=IgnoreNew`). Do not start
  a second copy by hand; a pending sell is never sent twice, but two workers
  would race.
- A sell rejected by the broker is not retried the same day.
- Do not modify `high52_positions` manually.
- Strategy rules stay private (`research/private/`).

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\high52-exit`.
2. Today's log: `logs\high52-exit-YYYYMMDD.log`.
3. `high52_positions` rows with `sell_status` `ordered` / `filled`.
