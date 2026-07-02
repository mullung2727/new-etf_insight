$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("close-bet-order-report-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

try {
  $report = & ".\.venv\Scripts\python.exe" "scripts\report_close_bet_order.py"
  $code = $LASTEXITCODE
  $report | Tee-Object -FilePath $log -Append | Write-Output
  if ($code -ne 0) {
    throw "report_close_bet_order.py failed with exit code $code"
  }
  & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $report 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  if ($LASTEXITCODE -ne 0) {
    throw "Discord report send failed with exit code $LASTEXITCODE"
  }
  exit 0
} catch {
  $message = "[close-bet order report] FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
