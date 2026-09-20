$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("close-bet-force-exit-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
# Task Scheduler fires at minute precision (09:01:30 in XML runs at 09:01:01).
# Wait so the worker's 09:01:00 chase-end market sell registers 'ordered' first.
Start-Sleep -Seconds 5
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

& ".venv\Scripts\python.exe" "scripts\run_close_bet_force_exit.py" `
  "--dry-run" "false" `
  "--broker-url" "http://localhost:8001" 2>&1 | Tee-Object -FilePath $log

exit $LASTEXITCODE
