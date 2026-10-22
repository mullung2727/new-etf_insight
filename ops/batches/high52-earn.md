# high52-earn

## Schedule

Schedule, timezone, Windows Task binding, legacy OpenClaw cron metadata, and
delivery live in `ops/batches/openclaw-cron.registry.json` (source of truth).
Do not duplicate them here.

## File Reading

Read this instruction file with a platform-neutral UTF-8-safe reader, such as
OpenClaw `file_fetch` or the agent's native workspace file access. Do not read
this file with host-default shell decoding, especially Windows PowerShell 5.1
`Get-Content` without an explicit UTF-8 setting.

## Purpose

19:40 earnings judgment (SUE) for the private high52 strategy's held
positions. It places no orders.

- Runs at 19:40, after `daily-financial-indicators` (19:00) loads earnings
  disclosures, quarterly report dates and accounts.
- For each held high52 position, judges the next unpublished quarter's
  operating-income SUE with the same function as the backtest; writes
  `earn_q` / `earn_sue` / `earn_verdict` / `earn_date` on `high52_positions`.
- `miss` (below the strategy threshold) or `unknown` (no number after the deadline grace, or
  SUE not computable) → the next day's `high52-exit` worker sells at market
  at 09:00. `hold` → judged again next quarter.
- No orders. Posts a Discord summary only when something was judged.
- 실패 시 수동 재실행: `.venv\Scripts\python.exe -m research.private.high52_quiet_breakout.live.earn_check --dry-run false` from `etl/` with `PYTHONPATH=..`.

## Guardrails

- Safe to rerun the same day (already-judged branches are skipped).
- Do not modify `high52_positions` manually.
- Strategy rules stay private (`research/private/`).

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\high52-earn`.
2. Today's log: `logs\high52-earn-YYYYMMDD.log`.
3. `high52_positions` rows in `db\watchlist.sqlite3`.
