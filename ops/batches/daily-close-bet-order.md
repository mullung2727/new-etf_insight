# daily-close-bet-order

## Cron

- Schedule: `19 15 * * 1-5`
- Timezone: `Asia/Seoul`
- OpenClaw session target: `isolated`
- Delivery: Discord announce

## Purpose

Run the close-bet order batch: read today's `llm_scores` from
`etl/db/watchlist.duckdb`, select symbols with `score >= 80` (sorted by score
descending, capped at `max_order_count=5`), and place Kiwoom market-price buy
orders for 1 share each.

This batch must only run after the 15:10 scoring batch (`daily-etf-watchlist-intraday-kiwoom`)
has completed. The precondition check at startup enforces this.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\docs\PLAN_WATCHLIST_CLOSE_BET.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\docs\PLAN_TRADE_HISTORY.md`

## Precondition Check

Before doing anything else, verify broker is running and today's scoring batch has run:

```powershell
Invoke-RestMethod http://localhost:8001/health
```

If this fails, broker is not running — report to Discord and stop:
```
[종가배팅] ABORT: broker(http://localhost:8001) 미기동 — 주문 불가.
```

Then verify today's scoring batch has run:

```powershell
@'
import duckdb, sys
from datetime import datetime
import pytz
date = datetime.now(pytz.timezone("Asia/Seoul")).strftime("%Y%m%d")
con = duckdb.connect("db/watchlist.duckdb", read_only=True)
cnt = con.execute(f"SELECT COUNT(*) FROM llm_scores WHERE date='{date}'").fetchone()[0]
con.close()
if cnt == 0:
    print(f"ABORT: llm_scores has 0 rows for {date} — scoring batch has not run today")
    sys.exit(1)
print(f"OK: {cnt} llm_scores rows found for {date}")
'@ | .\.venv\Scripts\python.exe -
```

If this exits with code 1, report to Discord:
```
[종가배팅] ABORT: {date} scoring 배치 미실행 — llm_scores 없음. 주문 건너뜀.
```
and stop immediately. Do not proceed to order.

## Execution

- Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.
- Use `.\.venv\Scripts\python.exe`; do not assume `uv` is on PATH.
- Load `.env` from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\.env` without printing secrets.
- Determine today's Asia/Seoul date as `YYYYMMDD`.
- Run after the precondition check passes:

```powershell
.\.venv\Scripts\python.exe scripts\run_close_bet.py --date <TODAY_YYYYMMDD>
```

Default parameters (override via CLI args as needed):
- `--score-threshold 80`
- `--max-order-count 5`
- `--qty-per-symbol 1`
- `--dry-run true` (flip to `false` for live paper trading)
- `--broker-url http://localhost:8001` (or set `BROKER_API_URL` env var)
- `--order-time 15:19:00`
- `--order-deadline-time 15:20:00`

## Order Rules

**핵심 사상: Kiwoom API는 무조건 broker를 통해서만 호출한다.**

- Query `llm_scores WHERE date=? AND score >= score_threshold`, order by score DESC, limit `max_order_count`.
- For each symbol, call `GET http://localhost:8001/quotes/{ticker}` via broker to get current price.
  - If price is None or request fails, skip and record in `close_bet_orders`.
- Before each order, check `now >= order_deadline_time` — if past deadline, stop immediately.
- Place buy order via `POST http://localhost:8001/orders` body `{symbol, side:"buy", qty, order_type:"market"}`.
  - Result recorded in both `close_bet_orders` and `kiwoom_trade_history` (universal trade ledger).
- Wait `order_interval_sec` (default 0.5s) between orders.
- Skip symbols already present in `close_bet_orders` for today (duplicate guard).
- Do NOT place orders outside `order_time <= now < order_deadline_time` in production.
  - Use `--allow-order-outside-close-window true` for test runs only.
- 동시호가(15:20~15:30) 중 시장가 주문은 금지 — `order_deadline_time=15:20:00`이 이를 보장.

## DB-First Reporting Rule

After the run, query `etl/db/watchlist.duckdb` directly:

```powershell
@'
import duckdb
date = "YYYYMMDD"
con = duckdb.connect("db/watchlist.duckdb", read_only=True)
rows = con.execute(
    f"SELECT ticker, score, status, order_no, message FROM close_bet_orders WHERE date='{date}' ORDER BY score DESC"
).fetchall()
con.close()
for r in rows:
    print(r)
'@ | .\.venv\Scripts\python.exe -
```

Report every row from `close_bet_orders` for today — do not summarize from stdout alone.

## Discord Report

Label:
```text
[종가배팅] {date} 주문 결과
```

For each order attempt, display:
- 종목명 `(ticker)`, score
- 현재가 (`cur_prc`) at time of order
- 주문 결과: `status` (submitted / skipped / failed)
- 주문번호: `order_no` if available
- 실패 사유: `message` if failed

Include totals: 주문 시도 N건, 성공 N건, 스킵 N건, 실패 N건.

On abort (precondition failure or all orders failed), report the exact blocker.

## PowerShell Safety

Do not use Bash heredoc syntax. For multiline Python, use:

```powershell
@'
print("ok")
'@ | .\.venv\Scripts\python.exe -
```
