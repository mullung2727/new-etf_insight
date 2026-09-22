# envelope-expire

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

Expiry batch for the private envelope strategy on the existing account broker
(`http://localhost:8001`). Starts 15:19: for positions at the holding limit it
cancels their take-profit order, then at 15:20:30 sends closing-auction market
sells (KRX). If the take-profit cancel, an order query, or the balance query fails,
that position is not sold today and is retried on the next trading day.

Strategy rules and parameters are private (`research/private/`, gitignored).

## Guardrails

- Sell quantity is capped by the ledger remainder and the sellable balance, so
  shares held by other strategies in the same ticker are never sold.
- A same-day rerun sends nothing (`exp_status='ordered'`).
- Do not modify `envelope_positions` manually.

## Verification

1. Windows task status for `\new-etf_insight\envelope-expire`.
2. Today's log: `logs\envelope-expire-YYYYMMDD.log`.
3. `envelope_positions.exp_status` of expiring rows.
