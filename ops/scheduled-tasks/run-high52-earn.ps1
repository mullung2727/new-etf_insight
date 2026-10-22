$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
. (Join-Path $PSScriptRoot "lib_trading_day_guard.ps1")
Exit-IfNonTradingDay -Tag "high52-earn"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = ".."

# high52 (private strategy) 19:40 earnings judgment (SUE) for held positions. No orders; the 09:00 exit worker sells miss/unknown. Runs after the 19:00 financial batch. Safe to rerun.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.high52_quiet_breakout.live.earn_check `
  "--dry-run" "false" `
  *> (Join-Path $logDir "high52-earn-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
