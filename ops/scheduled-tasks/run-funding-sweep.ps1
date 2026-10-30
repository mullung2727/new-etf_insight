$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
. (Join-Path $PSScriptRoot "lib_trading_day_guard.ps1")
Exit-IfNonTradingDay -Tag "funding-sweep"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
# research.private.* lives at the repo root; scripts.* resolves from etl (cwd).
$env:PYTHONPATH = ".."

# funding-sweep: shared funding engine on the existing account broker (:8001).
# Runs 15:21: sweep idle cash into CD ETF at the closing auction, wait for
# 15:31, confirm fills, post summary. No other orders.
# Same stderr handling as run-high52-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" scripts\funding_engine.py `
  "--broker-url" "http://localhost:8001" `
  *> (Join-Path $logDir "funding-sweep-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

# Exit contract with funding_engine.py: 0 ok; 1 python already sent the FAIL
# alert (no duplicate from the runner); 2 the alert itself failed.
if ($code -eq 2) { exit 2 }
if ($code -ne 0) { exit 1 }
exit 0
