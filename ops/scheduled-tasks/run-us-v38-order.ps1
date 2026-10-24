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

# US v38 (private strategy) order on the US account broker (:8003, in config).
# Starts 04:30 KST and waits internally until NY 15:45: market sell of carried
# positions, then buys. No Korean trading-day guard (independent of KRX
# holidays); US holidays exit quietly.
# LIVE ORDERS (user decision 2026-10-24: no dry-run period). Set --dry-run to true to stop live orders.
# The script posts its own Discord summary, so the runner sends no extra
# alert on failure (no duplicate notification). Same stderr handling as
# run-rebound-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.us_oas.live.order "--dry-run" "false" *> (Join-Path $logDir "us-v38-order-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
