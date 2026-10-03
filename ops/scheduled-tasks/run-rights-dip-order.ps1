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

# rights-dip (private strategy) order on the existing account broker (:8001).
# Starts 08:40 and waits internally to 08:50: TP sells + watering + first buys + withdrawal exits (KRX, day orders).
# DRY-RUN until R16 dry week is done (config dry_run). New first buys stop with enabled=false; TP/watering keep running.
# Same stderr handling as run-high52-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.rights_issue.live.order `
  "--broker-url" "http://localhost:8001" `
  *> (Join-Path $logDir "rights-dip-order-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
