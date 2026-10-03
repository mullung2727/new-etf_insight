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

# rights-dip (private strategy) detect on weekdays 08:20.
# KIND paid-in rights-offering filings -> candidates ledger (no orders). Needs daily OHLCV (ready by 08:20).
# Same stderr handling as run-high52-order.ps1 (PS 5.1 + redirected native stderr).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.rights_issue.live.detect `
  *> (Join-Path $logDir "rights-dip-detect-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
