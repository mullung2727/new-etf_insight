# rebound-order

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

Buy batch for the private rebound strategy on the existing account broker
(`http://localhost:8001`, shared cash with pullback and close-bet). The Windows
Task starts 08:40 and the batch waits internally: 09:00:05 market sell of
positions carried from the previous day, 09:00:20 open quotes, 09:01:00 limit
buys, 09:05:00 cutoff. Order size is today's available cash divided by three.
It posts its own Discord summary.

Strategy rules and parameters are private (`research/private/`, gitignored) and
must not be copied into this repository.

## Guardrails

- Live orders (`--dry-run false`, user decision 2026-09-20). Set it to `true` to
  stop live orders, or set `enabled` to `false` in the private config.
- Do not rerun the batch to "retry" orders. A second run on the same day is
  refused by the `rebound_runs` marker on purpose (no duplicate orders).
- Tickers already held in the account (any strategy) are skipped.
- Do not modify `rebound_positions` or `rebound_runs` manually.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\rebound-order`.
2. Today's log: `logs\rebound-order-YYYYMMDD.log`.
3. `rebound_positions` rows for today (`st1` / `st2`).
