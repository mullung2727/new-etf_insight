# daily-close-bet-verify

## Cron

- Schedule: `2 16 * * 1-5`
- Timezone: `Asia/Seoul`
- OpenClaw session target: `isolated`
- Delivery: Discord announce

## Execution Ownership

Windows Task Scheduler executes the verify script directly at 16:00.

- Task name: `\OpenClaw\close-bet-verify`
- Task definition: `ops/scheduled-tasks/close-bet-verify.xml`
- Script: `etl/scripts/run_verify.py`
- Log path: `etl/logs/close-bet-verify-YYYYMMDD.log`

OpenClaw cron wakes at 16:02 only to verify and report the completed result.
OpenClaw must not run `run_verify.py` or call the broker from this batch.

If the Windows scheduled task did not write a log yet, report that as the
blocker and do not execute the verify script manually.

## Purpose

Report the close-bet fill-verification result after the Windows scheduled task
has run.

The Windows task loads today's `close_bet_orders` rows with
`status IN ('submitted','unconfirmed')`, fetches the day's fills from the broker
(`GET /orders/history`, kt00007 경유), matches by `order_no` (0-padding
normalized), and updates each row to `confirmed` (with `cntr_price`, `cntr_qty`,
`verified_at`) or `unconfirmed`.

The 15:19 order batch (`daily-close-bet-order`) must have run first. Fills for
close-price market orders settle at 15:30, so 16:00 is after confirmation.

`kt00007` only returns the current day — there is no next-day re-check. Re-runs
on the same day re-process `unconfirmed` rows (idempotent).

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\ops\scheduled-tasks\close-bet-verify.xml`

## Result Verification

Work from:

```text
C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
```

Use `.\.venv\Scripts\python.exe`; do not assume `uv` is on PATH.

Determine today's Asia/Seoul date as `YYYYMMDD`.

Check the log:

```powershell
Get-Content C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl\logs\close-bet-verify-<YYYYMMDD>.log
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
    SELECT ticker, status, order_no, cntr_price, cntr_qty, verified_at
    FROM close_bet_orders
    WHERE date = ?
    ORDER BY status, ticker
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
[종가베팅] {date} 체결 대조 결과
```

For each order, display:

- 종목명 or ticker, status (`confirmed` / `unconfirmed`).
- For confirmed: 체결가(`cntr_price`), 체결수량(`cntr_qty`).
- Order number.

Include totals:

- 대조 대상 count
- confirmed count
- unconfirmed count

Highlight any `unconfirmed` rows as 확인 필요. On abort (broker fetch failed),
missing log, or all unconfirmed, report the exact blocker.

## PowerShell Safety

Do not use Bash heredoc syntax. For multiline Python, use:

```powershell
@'
print("ok")
'@ | .\.venv\Scripts\python.exe -
```
