# daily-new-etf-insight-batch

## Schedule

Schedule, timezone, Windows Task binding, legacy OpenClaw cron metadata, and
delivery live in `ops/batches/openclaw-cron.registry.json` (source of truth).
Do not duplicate them here.

## Purpose

Run the previous-day `new_etf_insight` ETF daily batch and sync records to DuckDB.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\skills\new-etf-insight-batch\SKILL.md`

## Execution

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Use `.\.venv\Scripts\python.exe`; do not assume `uv` is on PATH.
- Set `PYTHONPATH=src` before running Python (`$env:PYTHONPATH = "src"` in PowerShell).
- Load `.env` from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.env` without printing secrets.
- Determine yesterday's Asia/Seoul date as `YYYYMMDD`.
- Run `new_etf_insight.daily_pipeline.run_daily_pipeline` for that previous-day date with:
  - records path: `runs/YYYYMMDD/records`
  - PDFs path: `runs/YYYYMMDD/pdfs`
- The pipeline must sync records to SQLite at `etl/db/etf_insight.sqlite3`.

## Verification

After the pipeline:

- Verify `etl/db/etf_insight.sqlite3` exists.
- If DB row verification is needed, use the verification block in the
  skill exactly, replacing `YYYYMMDD`.
- Report a concise result summary to Discord.

If dependencies or env are missing, report the exact blocker and do not run
unrelated projects.

For production Windows Task runs, send the concise result summary through
`scripts/send_report_messages.py`, which sends to the channel set by
`NOTIFY_CHANNEL` (default discord via `DISCORD_WEBHOOK_URL`; see notify.py).

## PowerShell Safety

Do not use Bash heredoc syntax like:

```powershell
.\.venv\Scripts\python.exe - <<'PY'
```

For multiline Python verification in PowerShell, use:

```powershell
@'
print("ok")
'@ | .\.venv\Scripts\python.exe -
```
