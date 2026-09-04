$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null

$commonArgs = @(
  "--dry-run", "false",
  "--broker-url", "http://localhost:8001",
  "--poll-sec", "5",
  "--force-exit-time", "15:19:00",
  "--stop-time", "15:25:00",
  "--window-start", "09:00:00"
)

# -Wait 필수: 없으면 PS 5.1이 종료코드를 못 읽어 $null이 되고, $null -ne 0 이라 항상 rc=1
$pullback = Start-Process -FilePath (Join-Path $etlDir ".venv\Scripts\python.exe") `
  -ArgumentList (@("scripts\run_pullback_exit.py") + $commonArgs) `
  -WorkingDirectory $etlDir -WindowStyle Hidden -PassThru -Wait `
  -RedirectStandardOutput (Join-Path $logDir "pullback-exit-$date.out.log") `
  -RedirectStandardError (Join-Path $logDir "pullback-exit-$date.err.log")

if ($pullback.ExitCode -ne 0) { exit 1 }
exit 0
