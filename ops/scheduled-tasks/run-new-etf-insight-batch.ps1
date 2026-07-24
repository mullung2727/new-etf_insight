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
  # Native stderr is diagnostic output, not a failure signal. PowerShell 5.1 turns
  # redirected stderr into ErrorRecord objects, so judge native commands only by exit code.
  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

try {
  # 파이프라인의 상세 stdout은 로그로 격리하고, 알림 본문은 JSON 파일로 전달한다.
  # 긴 JSON/따옴표가 PowerShell 5.1 명령행 인자로 재해석되는 일을 막는다.
  $reportJson = Join-Path $etlDir ("runs\\" + $target + "\\report-messages.json")
  $runner = @'
import json
from contextlib import redirect_stdout
from pathlib import Path
from new_etf_insight.daily_pipeline import run_daily_pipeline

date = "__DATE__"
log_path = Path(r"__LOG__")
report_path = Path(r"__REPORT_JSON__")
with log_path.open("a", encoding="utf-8") as log_file:
    with redirect_stdout(log_file):
        result = run_daily_pipeline(
            date,
            date,
            Path("runs") / date / "records",
            Path("runs") / date / "pdfs",
        )
    log_file.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
actions = {}
for item in (result.get("results") or []):
    a = item.get("action") or "unknown"
    actions[a] = actions.get(a, 0) + 1
summary = (
    "[new_etf_insight daily] " + str(result.get("begin")) + "\n"
    + f"- candidates: {result.get('candidate_count')}\n"
    + f"- result actions: {actions}\n"
    + f"- DB synced: {result.get('db_synced')}\n"
    + f"- DB: {result.get('db_path')}"
)
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(
    json.dumps({"messages": [summary]}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(summary)
'@.Replace("__DATE__", $target).Replace("__LOG__", $log).Replace("__REPORT_JSON__", $reportJson)

  $previousErrorActionPreference = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $runner | .\.venv\Scripts\python.exe - 2>&1 |
      Tee-Object -FilePath $log -Append | Write-Output
    $pipelineCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($pipelineCode -ne 0) {
    throw "run daily pipeline failed with exit code $pipelineCode"
  }
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--json", $reportJson)
  exit 0
} catch {
  $message = "[new_etf_insight daily] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
