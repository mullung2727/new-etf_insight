$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# 크로스채널 종목 요약: discover(규칙) -> analyze(LLM) -> digest 전송.
# 수집(run-telegram-public-channel.ps1, 18:00)이 먼저 돈 뒤 실행 전제. 당일 KST 기준.
$target = (Get-Date).ToString("yyyy-MM-dd")
$session = "close"
$log = Join-Path $logDir ("telegram-stock-digest-" + (Get-Date).ToString("yyyyMMdd") + ".log")

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
  # 오케스트레이션(순서·에러전파)은 run_telegram_pipeline.py 로 이관(단위테스트 있음).
  Invoke-Step "telegram stock digest pipeline (discover->analyze->digest)" `
    ".\.venv\Scripts\python.exe" @("scripts\run_telegram_pipeline.py", "--date", $target, "--session", $session, "--channel", "telegram_report")
  exit 0
} catch {
  $message = "[Telegram 종목요약] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
