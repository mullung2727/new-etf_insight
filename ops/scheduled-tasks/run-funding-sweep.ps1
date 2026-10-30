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

# Exit contract with funding_engine.py: 0 ok; 3 python already sent FAIL
# alert (no duplicate from the runner); other codes need fallback notify.
if ($code -eq 0) { exit 0 }
if ($code -eq 3) { exit 1 }
# Fallback notify for unreported failures (1, 2, other) then exit 1.
& ".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, 'scripts'); from dotenv import load_dotenv; load_dotenv('../.env'); from notify import send_discord; send_discord(sys.argv[1])" "FAIL funding-sweep exit $code - see logs\funding-sweep-$date.log"
exit 1
