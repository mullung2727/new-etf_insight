# daily-close-bet-order

## Cron

- Schedule: `21 15 * * 1-5`
- Timezone: `Asia/Seoul`
- OpenClaw session target: `isolated`
- Delivery: Discord announce

## Execution Ownership

Windows Task Scheduler executes the order script directly at 15:19.

- Task name: `\OpenClaw\close-bet-order`
- Task definition: `ops/scheduled-tasks/close-bet-order.xml`
- Script: `etl/scripts/run_close_bet.py`
- Log path: `etl/logs/close-bet-YYYYMMDD.log`

OpenClaw cron wakes at 15:21 only to verify and report the completed result.
OpenClaw must not run `run_close_bet.py`, place orders, or retry order
execution from this batch.

If the Windows scheduled task did not write a log or DB rows yet, report that as
the blocker and do not execute the order script manually.

## Purpose

Report the close-bet order result after the Windows scheduled task has run.

The Windows task reads today's `llm_scores`, selects symbols with `score >= 80`
sorted by score descending with `max_order_count=5`, and places Kiwoom
market-price buy orders for 1 share each through the broker. It writes results
to `close_bet_orders` and `kiwoom_trade_history`.

The 15:10 scoring batch (`daily-etf-watchlist-intraday-kiwoom`) must complete
first. The order script performs its own precondition check and aborts if
today's `llm_scores` rows are missing.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\ops\scheduled-tasks\close-bet-order.xml`

## Result Verification

Work from:

```text
C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
```

Use `.\.venv\Scripts\python.exe`; do not assume `uv` is on PATH.

Determine today's Asia/Seoul date as `YYYYMMDD`.

Check the log:

```powershell
Get-Content C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\logs\close-bet-<YYYYMMDD>.log
```

Check the DB directly:

```powershell
@'
import duckdb
from datetime import datetime
import zoneinfo

date = datetime.now(zoneinfo.ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
db_path = r"C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\db\watchlist.duckdb"
con = duckdb.connect(db_path, read_only=True)
rows = con.execute(
    """
    SELECT ticker, score, status, order_no, message
    FROM close_bet_orders
    WHERE date = ?
    ORDER BY score DESC
    """,
    [date],
).fetchall()
print(f"total={len(rows)}")
for row in rows:
    print(row)
con.close()
'@ | C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\.venv\Scripts\python.exe -
```

Report from the log and DB rows. Do not report only from stdout or memory.

## Discord Report

Label:

```text
[종가베팅] {date} 주문 결과
```

For each order attempt, display:

- 종목명 or ticker, score, current price if available.
- Order result: `submitted`, `skipped`, or `failed`.
- Order number if available.
- Failure or skip reason from `message`.

Include totals:

- attempted count
- submitted count
- skipped count
- failed count

On abort, missing log, missing DB rows, or all failures, report the exact
blocker.

## PowerShell Safety

Do not use Bash heredoc syntax. For multiline Python, use:

```powershell
@'
print("ok")
'@ | .\.venv\Scripts\python.exe -
```
