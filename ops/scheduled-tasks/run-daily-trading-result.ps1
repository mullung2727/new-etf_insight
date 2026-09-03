param(
  [string]$Date
)

$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("daily-trading-result-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

try {
  $reportArgs = @("scripts\report_daily_trading_result.py", "--cause")
  if ($Date) {
    $reportArgs += @("--date", $Date)
  }
  $report = & ".\.venv\Scripts\python.exe" @reportArgs
  $code = $LASTEXITCODE
  $reportText = $report -join "`n"
  $reportText | Tee-Object -FilePath $log -Append | Write-Output
  if ($code -ne 0) {
    throw "report_daily_trading_result.py failed with exit code $code"
  }
  & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $reportText 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  if ($LASTEXITCODE -ne 0) {
    throw "daily trading result send failed with exit code $LASTEXITCODE"
  }
  exit 0
} catch {
  $message = "[daily trading result] FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
