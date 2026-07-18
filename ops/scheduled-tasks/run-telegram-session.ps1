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
# 하루 3회(10/16/24 = morning/close/evening) 등록. 각 세션은 같은 거래일 date_kst 를 공유해
# insights (date_kst, session) 이력이 하루 단위로 누적된다.
#
# 날짜: -3h 오프셋으로 자정 배치(00:00)를 방금 끝난 날(전일)에 귀속시킨다.
#   10:00 -> 07:00 당일 / 16:00 -> 13:00 당일 / 00:00 -> 21:00 전일 (세션 분기 없이 통일).
#   collect/discover/digest 전부 --date 를 인자로 받으므로 이 한 값이 체인 전체를 정한다.
$target = (Get-Date).AddHours(-3).ToString("yyyy-MM-dd")
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
  # 1) 수집: 대상 거래일 글 scrape (post_id 멱등 — 세션마다 다시 돌아도 새 글만 반영).
  Invoke-Step "collect telegram public channels ($target)" `
    ".\.venv\Scripts\python.exe" @("scripts\run_telegram_channels.py", "--date", $target)

  # 2) 파이프라인: discover -> analyze -> digest (오케스트레이션은 run_telegram_pipeline.py).
  Invoke-Step "telegram session pipeline ($Session, discover->analyze->digest)" `
    ".\.venv\Scripts\python.exe" @("scripts\run_telegram_pipeline.py", "--date", $target, "--session", $Session, "--channel", "telegram_report")

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
