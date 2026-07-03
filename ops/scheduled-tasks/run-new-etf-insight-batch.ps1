$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$target = (Get-Date).AddDays(-1).ToString("yyyyMMdd")
$log = Join-Path $logDir ("new-etf-insight-" + $target + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false  # BOM 없이 — here-string을 python - 로 파이프할 때 stdin U+FEFF 오염 방지
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
  # 파이프라인 실행 + 사람용 요약을 한 번의 python 호출로(멀티라인 -c 인자 깨짐 회피).
  # 상세 JSON은 파이썬이 직접 로그에 쓰고, stdout 은 Discord 보고용 요약만.
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
with open(r"__LOG__", "a", encoding="utf-8") as f:
    f.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
actions = {}
for item in (result.get("results") or []):
    a = item.get("action") or "unknown"
    actions[a] = actions.get(a, 0) + 1
print(
    "[new_etf_insight daily] " + str(result.get("begin")) + "\n"
    + f"- candidates: {result.get('candidate_count')}\n"
    + f"- result actions: {actions}\n"
    + f"- DB synced: {result.get('db_synced')}\n"
    + f"- DB: {result.get('db_path')}"
)
'@.Replace("__DATE__", $target).Replace("__LOG__", $log)

  $summary = $runner | .\.venv\Scripts\python.exe -
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
