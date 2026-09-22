# envelope-verify

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

Verify batch for the private envelope strategy on the existing account broker
(`http://localhost:8001`). Runs 16:00, sends no orders: confirms today's buys
(kt00007), settles take-profit and expiry sells, marks positions missing from the
balance, increments holding days on confirmed market days, and posts the daily
summary with warnings (missed expiry, allocation over the cap).

Strategy rules and parameters are private (`research/private/`, gitignored).

## Guardrails

- Safe to rerun: holding days increase once per day.
- Fills without a known order number are matched by ticker after excluding the
  order numbers in the rebound, close-bet and pullback ledgers.

## Verification

1. Windows task status for `\new-etf_insight\envelope-verify`.
2. Today's log: `logs\envelope-verify-YYYYMMDD.log`.
3. `envelope_positions` (`status`, `sold_qty`, `hold_days`, `close_reason`).
