$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = ".."

# US early daily load 06:30 KST: signal ETFs + extra tickers from Kiwoom via
# broker :8001, VIX/VIX3M from CBOE. No Korean trading-day guard (independent
# of KRX holidays); US holidays exit quietly via the broker latest-date check.
# Safe to rerun (upsert). Same stderr handling as run-high52-verify.ps1.
$log = Join-Path $logDir "us-early-$date.log"
# Extra tickers: env US_EARLY_EXTRA_TICKERS, else the private list file if present (scheduled tasks do not load .env).
$extraArgs = @()
$extraFile = $env:US_EARLY_EXTRA_TICKERS
if (-not $extraFile) {
  $extraFile = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\research\private\us_oas\early_tickers.txt"
}
if ($extraFile -and (Test-Path $extraFile)) {
  $extraArgs = @("--extra-tickers-file", $extraFile)
}
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" "scripts\build_us_early.py" @extraArgs *> $log
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) {
  $ErrorActionPreference = "Continue"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" "[us-early] FAILED (log: $log)" "--best-effort" 2>&1 | Tee-Object -FilePath $log -Append | Out-Null
  } catch {
  }
  $ErrorActionPreference = "Stop"
  exit 1
}
exit 0
