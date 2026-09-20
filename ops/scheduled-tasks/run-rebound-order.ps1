$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
# research.private.* lives at the repo root; scripts.* resolves from etl (cwd).
$env:PYTHONPATH = ".."

# rebound (private strategy) order on the existing account broker (:8001).
# Starts 08:40 and waits internally: 09:00:05 carried sells, 09:00:20 open quotes, 09:01:00 limit buys, 09:05 cutoff.
# LIVE ORDERS from the start (user decision 2026-09-20: no dry-run period). Set "--dry-run" to "true" to stop live orders.
# Same stderr handling as run-high52-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.intraday_rebound.live.order `
  "--dry-run" "false" "--broker-url" "http://localhost:8001" `
  *> (Join-Path $logDir "rebound-order-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
