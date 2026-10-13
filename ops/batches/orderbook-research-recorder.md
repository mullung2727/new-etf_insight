# orderbook-research-recorder

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

Mon-Fri 08:20 start; research orderbook (0D) + trade (0B) recorder for the
prior-day volume top 30 on the existing account broker (`http://localhost:8001`,
dedicated WS appkey). The script waits until 08:30, records 08:30-20:00, then
ends itself. Holiday detection is done by the script.

실매매 보호: broker WS 끊김·SSE 끊김·1000 Bye 시 당일 녹화 중단, 재접속 최대 20회, C: 여유 5GB 미만이면 중단.

See `docs/PLAN_ORDERBOOK_RESEARCH_RECORDING.md` for the design.

No orders are placed.

## Verification

Work from `C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl`.

1. Windows task status for `\new-etf_insight\orderbook-research-recorder`.
2. Today's log: `logs\orderbook-research-recorder-YYYYMMDD.log`.
3. Exit code 0 means normal (window closed or holiday); 3 means safe stop
   (the script already sent its notice).
