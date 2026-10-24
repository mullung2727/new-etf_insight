# swing-order

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

Pre-open buy batch for the private swing strategy on the existing account
broker (`http://localhost:8001`). The Windows Task starts 08:40; the batch
waits to 08:30:05 when started early and refuses to run after 08:59:30. It
places pre-open KRX limit buys for the previous evening's swing-pick top
candidates, one share each, and posts its own Discord summary to the swing
channel (`SWING_TRADE_DISCORD_WEBHOOK_URL`).

Strategy rules and parameters are private (`research/private/`, gitignored) and
must not be copied into this repository.

## Guardrails

- Live orders (`--dry-run false`, user decision 2026-10-24). Set `enabled` to
  `false` in the private config to stop.
- Do not rerun the batch to "retry" orders. A second run on the same day is
  refused by the `swing_trade_runs` marker on purpose (no duplicate orders).
- Do not modify `swing_trade_positions` manually.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\swing-order`.
2. Today's log: `logs\swing-order-YYYYMMDD.log`.
3. `swing_trade_positions` rows for today.
