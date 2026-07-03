$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$target = (Get-Date).AddDays(-1).ToString("yyyyMMdd")
$log = Join-Path $logDir ("krx-ohlcv-" + $target + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  & $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  $code = $LASTEXITCODE
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

try {
  Invoke-Step "build KRX OHLCV" ".\.venv\Scripts\python.exe" @("scripts\build_krx_ohlcv.py", "--date", $target)
  $report = @'
import duckdb
date = "__DATE__"
db = "db/krx_ohlcv.duckdb"
con = duckdb.connect(db, read_only=True)
row = con.execute(
    "SELECT COUNT(*) AS cnt, MIN(ticker) AS min_t, MAX(ticker) AS max_t, "
    "SUM(trading_value) AS total_tv FROM ohlcv WHERE date=?",
    [date],
).fetchone()
con.close()
cnt, min_t, max_t, total_tv = row
total_tv = total_tv or 0
print(
    "[KRX OHLCV] " + date + "\n"
    + f"- rows: {cnt}\n"
    + f"- ticker range: {min_t}~{max_t}\n"
    + f"- total trading value: {total_tv:,.0f}\n"
    + f"- DB: {db}\n"
    + "- watchlist build / LLM scoring: skipped by design"
)
'@.Replace("__DATE__", $target)
  $message = $report | .\.venv\Scripts\python.exe -
  $message | Tee-Object -FilePath $log -Append | Write-Output
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $message)
  exit 0
} catch {
  $message = "[KRX OHLCV] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
