# new_etf_insight Batch Run Guide

Use this skill when running or debugging the `new_etf_insight` ETF batch pipeline.

## Core Rules

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Do not run batch commands from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight`.
- Do not manually activate `.venv`.
- The uv-managed virtual environment is `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\.venv`, not `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.venv`.
- Use `uv sync` to prepare dependencies, then run commands through `uv run`.
- `pyproject.toml` is in the `etl` directory.
- Python requirement is `>=3.12`.
- `.env` is one directory above the working directory: `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.env`.
- Do not print secrets from `.env`.
- Batch output goes under `etl\runs\...`.
- DuckDB output goes to `etl\db\etf_insight.duckdb`.

## Prepare

```powershell
cd C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
uv sync
```

## Daily Batch

Replace `YYYYMMDD` with the target date.

```powershell
uv run python -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; print(run_daily_pipeline('YYYYMMDD', 'YYYYMMDD', Path('runs/YYYYMMDD/records'), Path('runs/YYYYMMDD/pdfs')))"
```

## Period Batch

Replace `BEGIN` and `END` with `YYYYMMDD` dates.

```powershell
uv run python -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; print(run_daily_pipeline('BEGIN', 'END', Path('runs/BEGIN-END/records'), Path('runs/BEGIN-END/pdfs')))"
```

## Smoke Test Batch

Use this for a quick limited run.

```powershell
uv run python -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; print(run_daily_pipeline('20260429', '20260429', Path('runs/smoke/records'), Path('runs/smoke/pdfs'), max_pages=2, query='KB RISE'))"
```

## Tests

```powershell
uv run python -m unittest tests/test_pipeline_modules.py
```

## Notes For Agents

- The project currently has no dedicated daily-pipeline CLI command.
- Run the batch by importing `new_etf_insight.daily_pipeline.run_daily_pipeline`.
- Keep generated `etl\runs`, `etl\db`, `etl\.venv`, and stackdump files out of unrelated commits.
