$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$runDate = Get-Date
$target = $runDate.ToString("yyyyMMdd")
$log = Join-Path $logDir ("us-facts-" + $target + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
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
  # 적재는 멱등이라 실패해도 다음 실행이 같은 날짜부터 다시 받는다.
  Invoke-Step "update us_facts" ".\.venv\Scripts\python.exe" @("scripts\build_us_facts.py")
  # 성공 알림은 생략한다 - 매일 오는 정상 알림은 소음이라 실패만 알린다.
  "[$(Get-Date -Format o)] us-facts 갱신 완료" | Tee-Object -FilePath $log -Append | Write-Output
  exit 0
} catch {
  $message = "[미국 재무 갱신] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "batch" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
