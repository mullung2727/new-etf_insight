# OpenClaw Batch Jobs

This directory keeps the project-owned source of truth for OpenClaw cron jobs.

OpenClaw cron still owns wake-up execution, but this project owns the intended
job configuration:

- `openclaw-cron.registry.json` — job name, id, schedule, timeout, delivery,
  and instruction file.
- `*.md` — execution steps, verification, and reporting rules for each job.

When schedule, timeout, delivery, or an instruction-file binding changes, update
`openclaw-cron.registry.json` first and then sync OpenClaw cron from that file.

## File reading

All files in this directory are UTF-8. OpenClaw agents should read these
instruction files with a platform-neutral file reader, such as OpenClaw
`file_fetch` or the agent's native workspace file access. Do not use shell
commands that depend on the host default encoding, especially Windows
PowerShell 5.1 `Get-Content` without an explicit UTF-8 setting, to read batch
instructions or Korean report bodies.

If Korean text appears corrupted while reading a batch instruction or report
body, stop using that read result and reread the file through a UTF-8-safe
reader before composing the Discord report.

## Jobs

- `daily-etf-watchlist-krx-ohlcv.md`
  - Purpose: fetch previous-day KRX full-market OHLCV only.
- `daily-etf-watchlist-intraday-kiwoom.md`
  - Purpose: build same-day Kiwoom intraday watchlist candidates and score them (pre-close, feeds 15:19 close-bet order window).
- `daily-new-etf-insight-batch.md`
  - Purpose: run the ETF daily insight pipeline and sync DuckDB.
- `daily-close-bet-order.md`
  - Purpose: report the Windows Task Scheduler 15:19 close-bet order result.

See `openclaw-cron.registry.json` for the active schedules and Discord delivery
target. Do not duplicate schedules in this README.

## Registry checks

Validate the project registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

Export desired OpenClaw cron specs from the registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-OpenClawCronSpecs.ps1
```

Export one job:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-OpenClawCronSpecs.ps1 -JobName daily-new-etf-insight-batch
```

## Windows Task Scheduler execution

Close-bet order/verify execution does not run through OpenClaw. Windows Task
Scheduler runs the scripts directly. OpenClaw may run report-only follow-up jobs
that inspect logs, scheduler status, and DB rows; those follow-up jobs must not
place or retry orders.

- `\OpenClaw\close-bet-order` — Mon-Fri 15:19, `etl/scripts/run_close_bet.py`
  (defined in `ops/scheduled-tasks/close-bet-order.xml`).
- `\OpenClaw\close-bet-verify` — Mon-Fri 16:00, `etl/scripts/run_verify.py`
  (defined in `ops/scheduled-tasks/close-bet-verify.xml`).
