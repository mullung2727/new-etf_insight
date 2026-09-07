$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("close-bet-exit-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 강제청산 시각은 여기서 주지 않는다 — etl/scripts/close_bet.json 의 exit_time 이 단일 소스다.
# stop-time 은 그 시각 이후 체결확인에 필요한 여유(약 9분).
& ".venv\Scripts\python.exe" "scripts\run_close_bet_exit.py" `
  "--dry-run" "false" `
  "--broker-url" "http://localhost:8001" `
  "--poll-sec" "3" `
  "--stop-time" "09:10:00" 2>&1 | Tee-Object -FilePath $log

exit $LASTEXITCODE
