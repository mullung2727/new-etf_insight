$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("close-bet-exit-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

& ".venv\Scripts\python.exe" "scripts\run_close_bet_exit.py" `
  "--dry-run" "false" `
  "--broker-url" "http://localhost:8001" `
  "--tp" "0.05" `
  "--sl" "0.03" `
  "--poll-sec" "3" `
  "--force-exit-time" "15:19:00" `
  "--stop-time" "15:25:00" 2>&1 | Tee-Object -FilePath $log

exit $LASTEXITCODE
