# us-v38-signal

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

10:25 KST Tue-Sat signal batch for the US private strategy, running after the
10:15 `us-early` load (after US after-hours ends, so the bars are final).

- Computes the daily signal from the freshly loaded US session data and
  appends one row per US session to the private signal log (read by
  `us-v38-order`, which uses the last row).
- No Korean trading-day guard: US holidays exit quietly.
- Exit 2 when the input data is stale. The signal module sends its own Discord
  alert (OK / stale / failure); the runner only turns a nonzero result into
  exit 1 and sends nothing.

Private strategy, rules in research/private (gitignored).

## Guardrails

- Do not rerun to "retry": a rerun appends a duplicate row for the same date.
- Do not hand-edit the private signal log; rerun only when the row is missing.
- Strategy names, rules, parameters, and tickers stay out of this file.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\us-v38-signal`.
2. Today's log: `logs\us-v38-signal-YYYYMMDD.log`.
3. The private signal log gained exactly one row for the latest US session.
