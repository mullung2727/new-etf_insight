# high52-order

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

Order batch for the private high52 strategy on its own account. It talks only to
the HIGH52 account broker (`http://localhost:8002`, see `broker/README.md`) and
never to the close-bet / pullback account broker (:8001).

The Windows Task starts at 15:10 KST and the batch waits internally:
15:18:00 universe quote, 15:19:00 decision, 15:19:10 orders, 15:20:00 cutoff.
It posts its own Discord summary.

Strategy rules and parameters are private (`research/private/`, gitignored) and
must not be copied into this repository.

## Guardrails

- The runner passes `--dry-run true`. Switching to `false` needs explicit user
  approval.
- Do not rerun the batch to "retry" orders. A second run on the same day is
  refused by the `high52_runs` marker on purpose (no duplicate orders).
- Do not modify `high52_positions`, `high52_runs`, or `high52_screen` manually.
- Restart brokers only with `scripts/restart_all_servers.ps1` (both :8001 and
  :8002 together).

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\high52-order`.
2. Today's log: `logs\high52-order-YYYYMMDD.log` (compact date key).
3. Rows in `db\watchlist.sqlite3`: `high52_runs` for today, `high52_screen`
   for today, and `high52_positions` created today.

A run that stops before ordering says why in the log and in its Discord
message (`주문 안 함: ...`), e.g. wrong broker profile, stale daily DB, market
day not confirmed, universe quote not finished by 15:19:00.
