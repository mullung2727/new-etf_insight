# rebound-exit

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

Exit worker for the private rebound strategy on the existing account broker
(`http://localhost:8001`). Starts 09:00 and polls every 5 seconds until 15:31:
keeps a target limit sell on every filled position (replaced after a second
buy fills, cancel first then re-order), cancels unfilled buys at 15:15, cancels
targets and sells at market at 15:19:20, retries the remainder once in the
closing auction at 15:20:10, and marks anything left as carried.

It acts only on its own ledger rows and order numbers; pullback and close-bet
positions in the same account are never touched.

## Guardrails

- One worker per day (task `MultipleInstancesPolicy=IgnoreNew`). Do not start
  a second copy by hand.
- If any broker lookup fails, that polling round does nothing.
- Do not modify `rebound_positions` manually.
- Strategy rules stay private (`research/private/`).

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\rebound-exit`.
2. Today's log: `logs\rebound-exit-YYYYMMDD.log`.
3. `rebound_positions` rows with `sell_status` `tp_open` / `close_ordered` / `filled` / `carried`.
