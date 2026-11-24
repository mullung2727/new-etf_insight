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
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m scripts.strategy_snapshot *> (Join-Path $logDir "strategy-snapshot-$date.log")
$code = $LASTEXITCODE
if ($code -ne 0) {
  & ".venv\Scripts\python.exe" scripts\send_report_messages.py --message "❌ strategy-snapshot 실패 (exit $code) — etl\logs\strategy-snapshot-$date.log"
}
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
