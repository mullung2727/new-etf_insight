# us-v38-order

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

Order batch for the US private strategy on the US account broker
(`:8003`). The Windows Task starts 04:30 KST Tue-Sat and the script waits
internally until NY 15:45 (KST 05:45 after DST ends): market sell of carried
positions, then buys from the latest private signal row. It posts its own
Discord summary.

No Korean trading-day guard: US holidays exit quietly.

Private strategy, rules in research/private (gitignored).

## Guardrails

- Live orders (`--dry-run false`, user decision 2026-10-24). Set it to `true`
  to stop live orders, or set `enabled` to `false` in the private config.
- Do not rerun the batch to "retry" orders. A second run on the same day
  risks duplicate orders.
- Tickers already held in the account (any strategy) are skipped.
- Do not hand-edit the private order state; the runner sends no extra alert
  on failure (the script notifies itself).

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\us-v38-order`.
2. Today's log: `logs\us-v38-order-YYYYMMDD.log`.
3. The script's own Discord summary for today's fills.
