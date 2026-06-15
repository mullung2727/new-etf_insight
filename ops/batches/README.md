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
- `daily-close-bet-order.md`
  - Schedule: Mon-Fri 15:19 Asia/Seoul
  - Purpose: place market-price buy orders for today's score >= 80 watchlist symbols (max 5). Requires 15:10 scoring batch to have run first.
- `daily-new-etf-insight-batch.md`
  - Schedule: daily 19:50 Asia/Seoul
  - Purpose: run the ETF daily insight pipeline and sync DuckDB.

