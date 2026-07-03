$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$target = (Get-Date).AddDays(-1).ToString("yyyyMMdd")
$log = Join-Path $logDir ("new-etf-insight-" + $target + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "src"

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
  $runner = @'
import json
from pathlib import Path
from new_etf_insight.daily_pipeline import run_daily_pipeline

date = "__DATE__"
result = run_daily_pipeline(
    date,
    date,
    Path("runs") / date / "records",
    Path("runs") / date / "pdfs",
)
print(json.dumps(result, ensure_ascii=False, indent=2))
'@.Replace("__DATE__", $target)

  $resultJson = $runner | .\.venv\Scripts\python.exe -
  $resultJson | Tee-Object -FilePath $log -Append | Write-Output

  $summary = $resultJson | .\.venv\Scripts\python.exe -c @'
import json, sys
data = json.load(sys.stdin)
results = data.get("results") or []
actions = {}
for item in results:
    actions[item.get("action") or "unknown"] = actions.get(item.get("action") or "unknown", 0) + 1
print(
    "[new_etf_insight daily] " + str(data.get("begin")) + "\n"
    + f"- candidates: {data.get('candidate_count')}\n"
    + f"- result actions: {actions}\n"
    + f"- DB synced: {data.get('db_synced')}\n"
    + f"- DB: {data.get('db_path')}"
)
'@
  $summary | Tee-Object -FilePath $log -Append | Write-Output
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $summary)
  exit 0
} catch {
  $message = "[new_etf_insight daily] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
