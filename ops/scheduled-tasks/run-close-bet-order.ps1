$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("close-bet-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

& ".venv\Scripts\python.exe" "scripts\run_close_bet.py" `
  "--score-threshold" "70" `
  "--max-order-count" "5" `
  "--qty-per-symbol" "1" `
  "--dry-run" "false" `
  "--broker-url" "http://localhost:8001" `
  "--order-time" "15:19:00" `
  "--order-deadline-time" "15:20:00" 2>&1 | Tee-Object -FilePath $log

exit $LASTEXITCODE
