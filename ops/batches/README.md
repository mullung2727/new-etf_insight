# OpenClaw Batch Jobs

This directory keeps the project-owned source of truth for OpenClaw cron jobs.

OpenClaw cron still owns scheduling and wake-up execution, but each cron payload
should read the matching file in this directory before running the batch. Keep
the runtime script, schedule, purpose, and reporting rules here so the batch can
be reviewed together with the project code.

## Jobs

- `daily-etf-watchlist-krx-ohlcv.md`
  - Schedule: Tue-Sat 08:00 Asia/Seoul
  - Purpose: fetch previous-day KRX full-market OHLCV only.
- `daily-etf-watchlist-intraday-kiwoom.md`
  - Schedule: Mon-Fri 15:10 Asia/Seoul
  - Purpose: build same-day Kiwoom intraday watchlist candidates and score them (pre-close, feeds 15:19 close-bet order window).
- `daily-new-etf-insight-batch.md`
  - Schedule: daily 19:50 Asia/Seoul
  - Purpose: run the ETF daily insight pipeline and sync DuckDB.

## Not OpenClaw batches (Windows Task Scheduler + self-reporting)

Close-bet order/verify no longer run through OpenClaw. Windows Task Scheduler
runs the scripts directly and they post their own Discord summary
(`DISCORD_WEBHOOK_URL`), so there is no OpenClaw report batch:

- `\OpenClaw\close-bet-order` — Mon-Fri 15:19, `etl/scripts/run_close_bet.py`
  (defined in `ops/scheduled-tasks/close-bet-order.xml`).
- `\OpenClaw\close-bet-verify` — Mon-Fri 16:00, `etl/scripts/run_verify.py`
  (defined in `ops/scheduled-tasks/close-bet-verify.xml`).
