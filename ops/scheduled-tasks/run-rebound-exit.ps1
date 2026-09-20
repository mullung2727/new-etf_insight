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

# rebound (private strategy) exit on the existing account broker (:8001).
# Starts 09:00, polls every 5s until 15:31: target limit sells, 15:15 buy cancel, 15:19:20 market close, 15:20:10 auction retry.
# Acts only on this strategy's own ledger rows (rebound_positions) and order numbers.
# Same stderr handling as run-high52-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.intraday_rebound.live.exit `
  "--broker-url" "http://localhost:8001" `
  *> (Join-Path $logDir "rebound-exit-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
