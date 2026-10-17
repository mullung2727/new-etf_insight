# orderbook-recorder

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

Orderbook snapshot recorder with two triggers (Mon-Fri 08:44 and 14:59); the
script picks the window by current time. Config:
`etl/scripts/orderbook_recorder.json` (`enabled=false` exits immediately).
Spec: `docs/done/SPEC_ORDERBOOK_SNAPSHOT_RECORDER.md` §11. No orders.
