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
  Invoke-Step "discover stock candidates" `
    ".\.venv\Scripts\python.exe" @("scripts\discover_telegram_stock_candidates.py", "--date", $target, "--session", $session)

  Invoke-Step "analyze candidates (LLM)" `
    ".\.venv\Scripts\python.exe" @("scripts\telegram_langgraph\telegram_analysis_langgraph.py", "--date", $target, "--session", $session)

  Invoke-Step "send stock digest" `
    ".\.venv\Scripts\python.exe" @("scripts\send_telegram_stock_digest.py", "--date", $target, "--session", $session, "--channel", "telegram_report")
  exit 0
} catch {
  $message = "[Telegram 종목요약] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
