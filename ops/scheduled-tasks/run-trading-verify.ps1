$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

& ".venv\Scripts\python.exe" "scripts\run_pullback_verify.py" `
  "--broker-url" "http://localhost:8001" `
  *> (Join-Path $logDir "pullback-verify-$date.log")
$pullbackExitCode = $LASTEXITCODE

if ($pullbackExitCode -ne 0) { exit 1 }
exit 0
