# new_etf_insight Batch Run Guide

Use this skill when running or debugging the `new_etf_insight` ETF batch pipeline.

## Core Rules

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Do not run batch commands from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight`.
- Do not manually activate `.venv`.
- The virtual environment is `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\.venv`, not `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.venv`.
- In this OpenClaw environment, `uv` may not be on PATH. Prefer `.\.venv\Scripts\python.exe` for batch and test execution.
- If `uv` is available, `uv sync` / `uv run` is acceptable, but do not fail solely because `uv` is missing while `.venv` exists.
- `pyproject.toml` is in the `etl` directory.
- Python requirement is `>=3.12`.
- `.env` is one directory above the working directory: `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.env`.
- Do not print secrets from `.env`.
- Batch output goes under `etl\runs\...`.
- DuckDB output goes to `etl\db\etf_insight.duckdb`.

## Prepare

```powershell
cd C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
.\.venv\Scripts\python.exe --version
```

## Daily Batch

Replace `YYYYMMDD` with the target date.

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; print(run_daily_pipeline('YYYYMMDD', 'YYYYMMDD', Path('runs/YYYYMMDD/records'), Path('runs/YYYYMMDD/pdfs')))"
```

## Period Batch

Replace `BEGIN` and `END` with `YYYYMMDD` dates.

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; print(run_daily_pipeline('BEGIN', 'END', Path('runs/BEGIN-END/records'), Path('runs/BEGIN-END/pdfs')))"
```

## Smoke Test Batch

Use this for a quick limited run.

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -c "from pathlib import Path; from new_etf_insight.daily_pipeline import run_daily_pipeline; print(run_daily_pipeline('20260429', '20260429', Path('runs/smoke/records'), Path('runs/smoke/pdfs'), max_pages=2, query='KB RISE'))"
```

## Tests

```powershell
$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m unittest tests/test_pipeline_modules.py
```

## DuckDB Verification

Use this PowerShell-compatible pattern when verifying DuckDB. Do not use Bash/Linux heredoc syntax such as `python - <<'PY'` in PowerShell.

```powershell
@'
import duckdb
import json
from pathlib import Path

records = sorted(Path("runs/YYYYMMDD/records").glob("*.json"))
db_path = Path("db/etf_insight.duckdb")
print(f"records={len(records)}")
print(f"db_exists={db_path.exists()}")

if records and db_path.exists():
    data = json.loads(records[-1].read_text(encoding="utf-8"))
    etf_key = data.get("source", {}).get("etf_key")
    fund_name = data.get("summary", {}).get("fund_name")
    con = duckdb.connect(str(db_path), read_only=True)
    record_count = con.execute("select count(*) from etf_records where etf_key = ?", [etf_key]).fetchone()[0]
    holding_count = con.execute("select count(*) from etf_holdings where etf_key = ?", [etf_key]).fetchone()[0]
    con.close()
    print(f"etf_key={etf_key}")
    print(f"fund_name={fund_name}")
    print(f"duckdb_record_count_for_key={record_count}")
    print(f"duckdb_holdings_count_for_key={holding_count}")
'@ | .\.venv\Scripts\python.exe -
```

## Notes For Agents

- The project currently has no dedicated daily-pipeline CLI command.
- Run the batch by importing `new_etf_insight.daily_pipeline.run_daily_pipeline`.
- In PowerShell, do not use `.\.venv\Scripts\python.exe - <<'PY'`; it fails before Python starts.
- Keep generated `etl\runs`, `etl\db`, `etl\.venv`, and stackdump files out of unrelated commits.
