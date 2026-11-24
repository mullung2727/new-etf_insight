# strategy-snapshot

## Schedule

Schedule, timezone, Windows Task binding, legacy OpenClaw cron metadata, and
delivery live in `ops/batches/openclaw-cron.registry.json` (source of truth).
Do not duplicate them here.

## Purpose

Daily per-strategy performance snapshot (`etl/scripts/strategy_snapshot.py`):
ledger fills + KRX regular-session closes → `strategy_daily` rows in
`<data root>/db/performance.sqlite3`. Design: `docs/PLAN_STRATEGY_PERFORMANCE.md` (P1).

Runs the morning after each trading day because `krx_ohlcv` only receives the
previous day's regular-session close in the 07:00 / 08:30 KRX OHLCV batch, and `us_ohlcv` gets the previous US day in us-early (10:15).

## Guardrails

- Read-only on strategy ledgers and broker state. Never places orders.
- Every run recomputes from each strategy's first fill, so reruns are safe.
- Manual check without writing: `cd etl && PYTHONPATH=.. .venv\Scripts\python.exe -m scripts.strategy_snapshot --dry-run --check`.
- Failure posts one alert via `scripts/send_report_messages.py`; success is silent.
