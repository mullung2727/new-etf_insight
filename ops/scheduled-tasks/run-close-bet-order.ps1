$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("close-bet-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$pythonArgs = @(
  "scripts\run_close_bet.py",
  "--dry-run",
  "false",
  "--broker-url",
  "http://localhost:8001",
  "--order-time",
  "15:19:00",
  "--order-deadline-time",
  "15:20:00"
)

$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" @pythonArgs *> $log
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if (Test-Path -LiteralPath $log) {
  Get-Content -Encoding UTF8 -LiteralPath $log | Write-Output
}

exit $code
