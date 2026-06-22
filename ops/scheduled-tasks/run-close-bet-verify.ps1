$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("close-bet-verify-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

& ".venv\Scripts\python.exe" "scripts\run_verify.py" `
  "--broker-url" "http://localhost:8001" 2>&1 | Tee-Object -FilePath $log

exit $LASTEXITCODE
