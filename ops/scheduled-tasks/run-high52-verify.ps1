$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = ".."

# high52 (private strategy) 16:00 fill confirmation and ledger update on the HIGH52 broker (:8002).
# No orders. Safe to rerun (idempotent). Same stderr handling as run-high52-order.ps1.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.high52_quiet_breakout.live.verify `
  "--broker-url" "http://localhost:8002" `
  *> (Join-Path $logDir "high52-verify-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
