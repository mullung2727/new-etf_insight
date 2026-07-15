$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# 매일 01:00 KST: 전일(어제) 등록 채널 전부 수집 → summary_mode=auto 미요약만 LLM.
# 날짜: 01:00 시점의 "어제" = 방금 끝난 캘린더 일자(KST).
$target = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
$log = Join-Path $logDir ("youtube-daily-" + (Get-Date).ToString("yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  # native stderr 한 줄로 PS 승격 방지 (telegram 러너와 동일)
  $prevEAP = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  & $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prevEAP
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

try {
  # 1) 전 채널 RSS+대본 (summary_mode 무관)
  Invoke-Step "youtube collect all channels ($target)" `
    ".\.venv\Scripts\python.exe" @("scripts\run_youtube_channels.py", "--date", $target)

  # 2) auto 채널 미요약만 (lookback 2일: 전일 실패분 재시도)
  Invoke-Step "youtube auto summarize ($target lookback 2)" `
    ".\.venv\Scripts\python.exe" @(
      "scripts\run_youtube_analysis.py",
      "--date", $target,
      "--auto-only",
      "--lookback-days", "2"
    )

  # ASCII-only: PS 5.1 parses UTF-8 no-BOM .ps1 as system ANSI (ks_c_5601) and mangles Korean literals.
  $okMsg = "[youtube-daily] $target OK (collect all + auto summarize)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $okMsg "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  exit 0
} catch {
  $message = "[youtube-daily] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
