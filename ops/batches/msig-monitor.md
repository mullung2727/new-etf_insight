# msig-monitor

## Schedule

Schedule, timezone, Windows Task binding, legacy OpenClaw cron metadata, and
delivery live in `ops/batches/openclaw-cron.registry.json` (source of truth).
Do not duplicate them here.

## Purpose

Monitor batch for the private Msig strategy.

Strategy rules and parameters are private (`research/private/afa/live/`) and
must not be copied into this repository.

## Guardrails

- `dry_run` is configured in `research/private/afa/live/config.json`.
- Alerts use the strategy-only webhook `MSIG_DISCORD_WEBHOOK_URL`.
