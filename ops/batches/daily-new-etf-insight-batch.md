# daily-new-etf-insight-batch

## Cron

- Schedule: `50 19 * * *`
- Timezone: `Asia/Seoul`
- OpenClaw session target: `isolated`
- Delivery: Discord announce

## Purpose

Run the `new_etf_insight` ETF daily batch and sync records to DuckDB.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\skills\new-etf-insight-batch\SKILL.md`

## Execution

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Use `.\.venv\Scripts\python.exe`; do not assume `uv` is on PATH.
- Set `PYTHONPATH=src` before running Python.
- Load `.env` from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.env` without printing secrets.
- Determine today's Asia/Seoul date as `YYYYMMDD`.
- Run `new_etf_insight.daily_pipeline.run_daily_pipeline` for that date with:
  - records path: `runs/YYYYMMDD/records`
  - PDFs path: `runs/YYYYMMDD/pdfs`
- The pipeline must sync records to DuckDB at `etl/db/etf_insight.duckdb`.

## Verification

After the pipeline:

- Verify `etl/db/etf_insight.duckdb` exists.
- If DB row verification is needed, use the DuckDB verification block in the
  skill exactly, replacing `YYYYMMDD`.
- Report a concise result summary to Discord.

If dependencies or env are missing, report the exact blocker and do not run
unrelated projects.

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
