$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# Night runner: collect telegram public channels -> theme re-emergence alert only.
# No stock analysis (no discover/analyze/digest). Runs daily at 21:00.
$target = (Get-Date).ToString("yyyy-MM-dd")
$log = Join-Path $logDir ("theme-alert-night-" + (Get-Date).ToString("yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  # Pitfall: with $ErrorActionPreference=Stop, `& native 2>&1` promotes even one
  # stderr line (e.g. codex startup banner) to a terminating error on exit 0.
  # Judge success by $LASTEXITCODE only: lower to Continue in this call block
  # (a real exit!=0 is still caught below).
  $prevEAP = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prevEAP
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

try {
  # 1) Collect: on failure log one line and continue (theme step runs on existing posts).
  try {
    Invoke-Step "collect telegram public channels ($target)" ".\.venv\Scripts\python.exe" @("scripts\run_telegram_channels.py", "--date", $target)
  } catch {
    "[$(Get-Date -Format o)] collect telegram skipped: $($_.Exception.Message)" | Tee-Object -FilePath $log -Append | Write-Output
  }

  # 2) Theme re-emergence alert (docs/PLAN_THEME_REEMERGENCE_ALERT.md). Failure fails the task.
  Invoke-Step "theme alert telegram (night, $target)" ".\.venv\Scripts\python.exe" @("scripts\run_theme_alert.py", "--source", "telegram", "--date", $target, "--session", "night")

  exit 0
} catch {
  $message = "[theme-alert-night] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "theme_alert" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
