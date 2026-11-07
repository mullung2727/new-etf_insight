# rl-signal-train

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

Mon-Fri 20:45; nightly RL-signal retrain + next-day walk-forward eval after the
20:15 orderbook archive lands (incremental, `--shuffle-reps 20`), then one
Discord alert with the day result + stratum cumulative check.

No orders are placed.

Stratum pass rules: `docs/PLAN_RL_SIGNAL.md` §10.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\rl-signal-train`.
2. Today's log: `logs\rl-signal-train-YYYYMMDD.log`.
3. Result json: `..\research\private\rl_signal\results\v2\hgb_adv\YYYYMMDD.json`.
4. Discord alert in the RL-signal channel (`RL_SIGNAL_DISCORD_WEBHOOK_URL`).
