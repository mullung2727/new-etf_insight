# monthly-db-backup

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

Mon 05:30; DB backup to the external drive (overwrite) via
`ops/scheduled-tasks/run-db-backup.ps1`. The runner sends its own
success/failure/drive-missing notification. Trigger is weekly despite the name.
