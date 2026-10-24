# weekly-db-backup

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

Sat 05:30; DB backup to the external drive (overwrite) via
`ops/scheduled-tasks/run-db-backup.ps1`, which runs `etl/scripts/backup_db.py` and
propagates its exit code. `backup_db.py` sends the success/failure/drive-missing
notification.

## daily-code-backup (`--code-only`)
- 매일 23:30 `run-code-backup.ps1` → `backup_db.py --code-only`.
- `.env`·`research/private` 등 C: 에 남는 파일을 `NEW_ETF_DATA_ROOT/code_backup/` 로 복사. 외장하드는 안 씀.
- `NEW_ETF_DATA_ROOT` 미설정이면 아무것도 안 하고 0 종료. 실패 때만 batch 채널 알림.
