# daily-close-bet-order

## Cron

- Schedule: `21 15 * * 1-5`
- Timezone: `Asia/Seoul`
- OpenClaw session target: `isolated`
- Delivery: Discord announce

## File Reading

Read this instruction file with a platform-neutral UTF-8-safe reader, such as
OpenClaw `file_fetch` or the agent's native workspace file access. Do not read
this file with host-default shell decoding, especially Windows PowerShell 5.1
`Get-Content` without an explicit UTF-8 setting.

If Korean text in this file appears corrupted, discard that read result and
reread with a UTF-8-safe reader before reporting to Discord.

## Purpose

Report the result of the Windows Task Scheduler close-bet order job.

The actual order job is `\OpenClaw\close-bet-order`, scheduled at 15:19 KST,
and runs `etl/scripts/run_close_bet.py` directly. This OpenClaw job is a
report-only follow-up around 15:21 KST.

## Required References

- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\AGENTS.md`
- `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\ops\scheduled-tasks\close-bet-order.xml`

## Guardrails

- Do not run `scripts\run_close_bet.py`.
- Do not place orders.
- Do not retry failed orders.
- Do not modify `close_bet_orders` manually.
- Report from logs, Windows Task Scheduler status, and DB rows only.

## Verification

Work from:

```powershell
C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl
```

Do not write ad hoc SQL for this report. Use the project-owned report script,
which calls the same status/query helpers as the order batch:

```powershell
.\.venv\Scripts\python.exe scripts\report_close_bet_order.py
```

Check the report script output for:

1. Windows task status for `\OpenClaw\close-bet-order`.
2. Today's log file, if present: `logs\close-bet-YYYYMMDD.log`.
3. Today's DB rows in `db\watchlist.sqlite3`:
   - `llm_scores` count for today.
   - `llm_scores` rows with `score >= 70`.
   - `close_bet_orders` rows for today.

Use the compact date key `YYYYMMDD` for DB queries and log filenames. The
watchlist DB stores `llm_scores.date` and `close_bet_orders.date` as compact
strings such as `20260629`, not dashed strings such as `2026-06-29`. If a
dashed date is used for display, convert it before querying. When reporting,
include the exact DB date key used.

Use the report script's DB-backed rows as the source of truth for whether
orders were attempted.

## Discord Report

Clearly label the report as:

```text
종가베팅 주문 배치 결과
```

Include:

- Target date.
- Windows task last run time and result code.
- Whether the log file exists.
- Candidate count with `score >= 70`.
- `close_bet_orders` count and each row's ticker, score, status, order number,
  and message.
- Final judgment: completed, no targets, failed before order, or blocked.

If `close_bet_orders` is empty while candidates existed, say the order did not
proceed and include the exact observed blocker.
