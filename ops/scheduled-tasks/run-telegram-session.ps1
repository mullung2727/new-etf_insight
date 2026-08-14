param(
  [ValidateSet("morning", "close", "evening")]
  [string]$Session = "close"
)
$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# 세션 러너: 수집(scrape) -> discover -> analyze(LLM) -> digest 를 한 세션분 실행.
# 운영은 하루 2회(10/16 = morning/close). morning은 전일 close 워터마크 이후 새 글을
# 놓치지 않도록 전일~당일 2일치를 수집·조회하되 결과는 당일 morning에 귀속한다.
#
# 결과 귀속일은 -3h 오프셋으로 계산한다. 10:00/16:00은 당일에 귀속되고,
# 비활성 상태로 남겨둔 evening을 수동 실행하면 기존처럼 방금 끝난 전일에 귀속된다.
# morning 조회 시작일만 전일로 넓히며 워터마크로 전날 close 이후 새 글만 분석한다.
$target = (Get-Date).AddHours(-3).ToString("yyyy-MM-dd")
$collectStart = $target
if ($Session -eq "morning") {
  $collectStart = [datetime]::ParseExact($target, "yyyy-MM-dd", $null).AddDays(-1).ToString("yyyy-MM-dd")
}
$log = Join-Path $logDir ("telegram-session-" + (Get-Date).ToString("yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  # $ErrorActionPreference=Stop + `& native 2>&1` 는 PowerShell 함정: 네이티브 프로세스가
  # stderr 에 한 줄만 써도(예: codex 시작 배너 "OpenAI Codex v0.142.5") exit 0 이어도
  # 종료에러로 승격돼 세션을 FAILED 로 만든다. 성공판정은 $LASTEXITCODE 로만 하도록
  # 이 호출 구간만 Continue 로 낮춘다(진짜 exit≠0 는 아래에서 여전히 잡힘).
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
  # 1) 수집: morning은 전일~당일, close는 당일 scrape (post_id 멱등).
  $collectArgs = @("scripts\run_telegram_channels.py", "--date", $collectStart)
  if ($collectStart -ne $target) { $collectArgs += @("--end", $target) }
  Invoke-Step "collect telegram public channels ($collectStart~$target)" `
    ".\.venv\Scripts\python.exe" $collectArgs

  # 2) 파이프라인: discover -> analyze -> digest (오케스트레이션은 run_telegram_pipeline.py).
  $pipelineArgs = @("scripts\run_telegram_pipeline.py", "--date", $target, "--session", $Session, "--channel", "telegram_report")
  if ($collectStart -ne $target) { $pipelineArgs += @("--start-date", $collectStart) }
  Invoke-Step "telegram session pipeline ($Session, $collectStart~$target, discover->analyze->digest)" `
    ".\.venv\Scripts\python.exe" $pipelineArgs

  # 3) 하루 롤업: evening(마지막 세션) 뒤 3세션 합산 'TOP N 주목 종목' 전송. 그 외 세션은 스킵.
  if ($Session -eq "evening") {
    Invoke-Step "telegram daily rollup ($target)" `
      ".\.venv\Scripts\python.exe" @("scripts\send_telegram_daily_rollup.py", "--date", $target, "--channel", "telegram_report")
  }
  exit 0
} catch {
  $message = "[Telegram 세션요약] $target/$Session FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
