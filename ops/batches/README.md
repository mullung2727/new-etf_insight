# Project Batch Jobs

This directory keeps the project-owned source of truth for production batch jobs.

Windows Task Scheduler is the primary executor for production batches, so the
jobs keep running even when the OpenClaw gateway is down. OpenClaw cron specs
are kept as legacy/fallback metadata for diagnostics and manual recovery.

- `openclaw-cron.registry.json` — job name, id, schedule, timeout, delivery,
  instruction file, and Windows Task binding.
- `*.md` — execution steps, verification, and reporting rules for each job.
- `../scheduled-tasks/*.ps1` — direct batch runners.
- `../scheduled-tasks/*.xml` — Windows Task Scheduler definitions.

When schedule, timeout, delivery, or an instruction-file binding changes, update
`openclaw-cron.registry.json` first and then export/register the Windows task
definitions from that file. Keep OpenClaw cron synchronized only if you still
want the legacy fallback jobs.

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
  - Purpose: build same-day Kiwoom candidates and write D+1 open-rise probability scores to `llm_scores` (feeds the 15:19 close-bet order window).
- `daily-new-etf-insight-batch.md`
  - Purpose: run the ETF daily insight pipeline and sync DuckDB.
- `daily-close-bet-order.md`
  - Purpose: report the Windows Task Scheduler 15:19 close-bet order result.

See `openclaw-cron.registry.json` for the active schedules, Windows task
bindings, and Discord webhook env key. Do not duplicate schedules in this README.

## Registry checks

Validate the project registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Test-OpenClawBatchRegistry.ps1
```

Export desired OpenClaw cron specs from the registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-OpenClawCronSpecs.ps1
```

Export Windows Task Scheduler registration specs from the registry:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-WindowsScheduledTaskSpecs.ps1
```

Export registration commands only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-WindowsScheduledTaskSpecs.ps1 -CommandsOnly
```

Export one job:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\batches\Export-OpenClawCronSpecs.ps1 -JobName daily-new-etf-insight-batch
```

## Windows Task Scheduler execution

Production execution does not rely on OpenClaw. Windows Task Scheduler runs the
project scripts directly and the scripts report through `DISCORD_WEBHOOK_URL`.

- `\new_etf_insight\daily-new-etf-insight-batch` — daily 07:00,
  `ops/scheduled-tasks/run-new-etf-insight-batch.ps1`.
- `\new_etf_insight\daily-etf-watchlist-krx-ohlcv` — Tue-Sat 08:00,
  `ops/scheduled-tasks/run-krx-ohlcv.ps1`.
- `\new-etf_insight\daily-etf-watchlist-intraday-kiwoom` — Mon-Fri 14:59 시작, 15:00 스냅샷,
  `ops/scheduled-tasks/run-watchlist-intraday.ps1`.
- `\new_etf_insight\daily-close-bet-order-report` — Mon-Fri 15:21,
  `ops/scheduled-tasks/run-close-bet-order-report.ps1`.
- `\OpenClaw\close-bet-order` — Mon-Fri 15:19, `etl/scripts/run_close_bet.py`
  (defined in `ops/scheduled-tasks/close-bet-order.xml`).
- `\OpenClaw\close-bet-verify` — Mon-Fri 16:00, `etl/scripts/run_verify.py`
  (defined in `ops/scheduled-tasks/close-bet-verify.xml`).

The 15:21 report task is report-only and must not place or retry orders.
