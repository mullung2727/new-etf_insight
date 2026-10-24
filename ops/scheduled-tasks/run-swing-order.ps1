$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
. (Join-Path $PSScriptRoot "lib_trading_day_guard.ps1")
Exit-IfNonTradingDay -Tag "swing-order"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
# research.private.* lives at the repo root; scripts.* resolves from etl (cwd).
$env:PYTHONPATH = ".."

# private swing strategy pre-open buy batch on :8001.
# Starts 08:40 (waits to 08:30:05 if early, refuses after 08:59:30).
# LIVE ORDERS from the start (user decision 2026-10-24). Set "--dry-run" to "true" to stop live orders.
# Same stderr handling as run-high52-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.swing_trade.live.order `
  "--dry-run" "false" "--broker-url" "http://localhost:8001" `
  *> (Join-Path $logDir "swing-order-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
