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

# US v38 signal 07:40 KST, after the 07:30 us-early load. Appends one row per
# US session to the private signal log; a rerun appends a duplicate row.
# No Korean trading-day guard (independent of KRX holidays); US holidays exit
# quietly. Exit 2 on stale data. Same stderr handling as run-us-early.ps1.
$log = Join-Path $logDir "us-v38-signal-$date.log"
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.private.us_oas.signal_v38 *> $log
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) {
  $ErrorActionPreference = "Continue"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" "[us-v38-signal] FAILED (log: $log)" "--best-effort" 2>&1 | Tee-Object -FilePath $log -Append | Out-Null
  } catch {
  }
  $ErrorActionPreference = "Stop"
  exit 1
}
exit 0
