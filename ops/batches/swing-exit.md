# swing-exit

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

Exit worker for the private swing strategy on the existing account broker
(`http://localhost:8001`). Runs 09:00-15:31 and sells only this strategy's own
ledger quantity: other strategies may hold the same ticker in the same account,
and those shares are never touched. At most one market order per position per
day.

## Guardrails

- One worker per day (task `MultipleInstancesPolicy=IgnoreNew`). Do not start
  a second copy by hand.
- Sells only this strategy's ledger quantity (`swing_trade_positions`); shares
  held by other strategies in the same account are never touched.
- Do not modify `swing_trade_positions` manually.
- Strategy rules stay private (`research/private/`).

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\swing-exit`.
2. Today's log: `logs\swing-exit-YYYYMMDD.log`.
3. `swing_trade_positions` rows for today's exits.
