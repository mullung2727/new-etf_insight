# daily-etf-watchlist-krx-ohlcv

## Cron

Schedule, timezone, session target, and delivery live in
`ops/batches/openclaw-cron.registry.json` (source of truth). Do not duplicate
them here.

## Purpose

Run the ETF watchlist morning KRX OHLCV fetch-only batch for the previous
Asia/Seoul calendar date.

This is the next-morning KRX price-only batch. KRX OpenAPI may publish the
previous trading day's full exchange OHLCV one day late.

This job must only refresh and verify `etl/db/krx_ohlcv.duckdb` for the previous
date.

Do not select stock picks, do not build or update `etl/db/watchlist.sqlite3`, do
not run `run_watchlist_research.py`, do not collect board/news/web evidence, do
not create LLM scores, and do not write watchlist research reports. Stock
picking and LLM scoring belong to the separate same-day 15:35 Kiwoom intraday
batch.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`

## Execution

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Use `.\.venv\Scripts\python.exe`; do not assume `uv` is on PATH.
- Load `.env` from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.env` without printing secrets.
- Determine yesterday's Asia/Seoul date as `YYYYMMDD`.
- Run exactly:

```powershell
.\.venv\Scripts\python.exe scripts\build_krx_ohlcv.py --date <YESTERDAY_YYYYMMDD>
```

## DB-First Reporting Rule

After the runner finishes, query `etl/db/krx_ohlcv.duckdb` directly and report
from the saved DB rows, not from script stdout alone.

Use this verification block (replace `YYYYMMDD`):

```powershell
@'
import duckdb
con = duckdb.connect("db/krx_ohlcv.duckdb", read_only=True)
date = "YYYYMMDD"
row = con.execute(
    f"SELECT COUNT(*) AS cnt, MIN(ticker) AS min_t, MAX(ticker) AS max_t, "
    f"SUM(trading_value) AS total_tv FROM ohlcv WHERE date='{date}'"
).fetchone()
print(f"date={date} rows={row[0]} ticker_range=[{row[1]}~{row[2]}] total_trading_value={row[3]:,.0f}")
'@ | .\.venv\Scripts\python.exe -
```

Verify:

- OHLCV row count for the target date.
- Ticker range (`min_t` ~ `max_t`) as a sanity check.
- Total `trading_value`.

Do not query or report watchlist/LLM scores except to explicitly say this job
skipped them by design.

## Discord Report

Keep it concise. Include:

- Target date.
- KRX OHLCV row count.
- Whether rows were fetched or already present according to script output.
- DB path.
- Confirmation that watchlist build and LLM scoring were not run.

On non-trading day or KRX empty data, announce a concise skip/empty message for
the explicit target date. On failure, report the exact blocker and do not run
unrelated projects.

## PowerShell Safety

Do not use Bash heredoc syntax. For multiline Python, use:

```powershell
@'
print("ok")
'@ | .\.venv\Scripts\python.exe -
```

