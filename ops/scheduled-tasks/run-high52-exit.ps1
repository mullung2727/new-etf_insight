$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = ".."

# high52 (private strategy) intraday exit worker on the HIGH52 broker (:8002).
# Starts 08:50, judges from 09:00, stops 15:18:30 (the order batch owns the account after that).
# LIVE sells (user approved 2026-09-14). Set "--dry-run" to "true" to stop live sells.
# Same stderr handling as run-high52-order.ps1.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.high52_quiet_breakout.live.exit `
  "--dry-run" "false" "--broker-url" "http://localhost:8002" `
  *> (Join-Path $logDir "high52-exit-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
