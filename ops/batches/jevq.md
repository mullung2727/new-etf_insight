# jevq (7 tasks)

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

Private strategy on the existing account broker (`http://localhost:8001`).
Seven tasks share this file: exit, report, outcomes, prep, order, buy-ah,
add-ah (see the registry for schedules and runners). Runners, XMLs, and docs
live in `research/private/jev_quant_live/ops/` (gitignored, not in this repo);
see its README.md.
