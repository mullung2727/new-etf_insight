$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null

$commonArgs = @(
  "--dry-run", "false",
  "--broker-url", "http://localhost:8001",
  "--poll-sec", "3",
  "--force-exit-time", "15:19:00",
  "--stop-time", "15:25:00"
)

$pullback = Start-Process -FilePath (Join-Path $etlDir ".venv\Scripts\python.exe") `
  -ArgumentList (@("scripts\run_pullback_exit.py") + $commonArgs) `
  -WorkingDirectory $etlDir -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $logDir "pullback-exit-$date.out.log") `
  -RedirectStandardError (Join-Path $logDir "pullback-exit-$date.err.log")

$pullback.WaitForExit()
if ($pullback.ExitCode -ne 0) { exit 1 }
exit 0
