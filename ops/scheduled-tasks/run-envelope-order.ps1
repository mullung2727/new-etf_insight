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

# envelope (private strategy) order on the existing account broker (:8001).
# Starts 08:20 and waits internally: daily OHLCV (until 08:45), 08:50 TP sells + open limit buys, 08:59 cutoff, 09:00:10 cancel unfilled buys.
# LIVE ORDERS (user decision 2026-09-22: no dry-run). New buys stop with enabled=false in the private config; TP sells keep running.
# Same stderr handling as run-high52-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.envelope.live.order `
  "--broker-url" "http://localhost:8001" `
  *> (Join-Path $logDir "envelope-order-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
