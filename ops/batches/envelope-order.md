# envelope-order

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

Order batch for the private envelope strategy on the existing account broker
(`http://localhost:8001`, shared cash with rebound, pullback and close-bet). The
Windows Task starts 08:20 and the batch waits internally: it waits for the daily
OHLCV load (gives up at 08:45), computes signals, then at 08:50 sends take-profit
limit sells for held positions and pre-open limit buys (KRX), 08:59 cutoff, and
at 09:00:10 cancels any unfilled buy remainder. It posts its own Discord summary.

Strategy rules and parameters are private (`research/private/`, gitignored) and
must not be copied into this repository.

## Guardrails

- Live orders (user decision 2026-09-22: no dry-run period). Set `enabled` to
  `false` in the private config to stop new buys; take-profit sells, expiry and
  verify keep running on purpose (positions have no stop-loss).
- Do not rerun the batch to "retry" orders. A second run on the same day is
  refused by the `envelope_runs` marker on purpose (no duplicate orders).
- Tickers already held in the account (any strategy) are not bought.
- Non-trading days exit immediately without writing `envelope_runs`.
- Do not modify `envelope_positions` or `envelope_runs` manually.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\envelope-order`.
2. Today's log: `logs\envelope-order-YYYYMMDD.log`.
3. `envelope_positions` rows for today (`status`, `tp_status`) and `envelope_runs`.
