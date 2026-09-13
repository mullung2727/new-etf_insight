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

# high52 (private strategy) order batch on the HIGH52 account broker (:8002).
# Starts 15:10 and waits internally: 15:18:00 universe quote, 15:19:00 decision, 15:19:10 orders, 15:20:00 cutoff.
# LIVE ORDERS since 2026-09-14 (user approved; the dry-run comparison was skipped by user decision).
# Set "--dry-run" back to "true" to stop live orders.
#
# The module prints pandas warnings to stderr. Under PS 5.1, "Stop" + redirected native stderr
# turns those lines into terminating errors and kills the runner even when python exits 0,
# so the call runs with "Continue" and the exit code decides success.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.high52_quiet_breakout.live.order `
  "--dry-run" "false" "--broker-url" "http://localhost:8002" `
  *> (Join-Path $logDir "high52-order-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
