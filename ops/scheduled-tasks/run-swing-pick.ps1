$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# 이 파일은 UTF-8 BOM 으로 저장할 것 (2026-09-04 사건: 무BOM이면 PS5.1이 CP949로 읽어 한글 깨짐).
# 19:00 스윙 후보 3종목 선정. 시각 근거: 텔레그램 close 16:00, 유튜브 evening 18:00,
# 네이버 리서치 18:00 이후라 당일분이 다 모인다.
# 휴장일 판정은 파이썬 쪽(run_swing_pick.py 시작 시 exit 0). 날짜 인자 없음.
# 성공 보고는 파이썬이 직접 channel=batch 로 보낸다. 러너는 실패 알림만.
$target = (Get-Date).ToString("yyyy-MM-dd")
$log = Join-Path $logDir ("swing-pick-" + (Get-Date).ToString("yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false  # BOM 없이 — here-string을 python - 로 파이프할 때 stdin U+FEFF 오염 방지
$env:PYTHONUTF8 = "1"

# Tee-Object 는 PS5.1 에서 -Encoding 을 받지 않아 UTF-16LE 로 쓴다. UTF-8 로 직접 붙인다.
$logEncoding = New-Object System.Text.UTF8Encoding $false
function Write-Log {
  param([string]$Text)
  # AppendAllText 실패는 .NET 예외라 EAP 와 무관하게 종료성이다. 로그를 못 써서
  # 실패 알림까지 막히면 배치가 조용히 죽는다. 기록 실패는 삼킨다.
  try { [System.IO.File]::AppendAllText($log, $Text + [Environment]::NewLine, $logEncoding) } catch {}
  Write-Output $Text
}

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  Write-Log "[$(Get-Date -Format o)] $Label"
  # EAP=Stop 이면 네이티브 명령의 stderr 가 NativeCommandError 로 던져져, Tee/로그가 한 줄도
  # 남기지 못한 채 catch 로 튄다. 이 구간만 Continue 로 내려 stderr 를 로그에 남긴다.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Exe @StepArgs 2>&1 | ForEach-Object { Write-Log ([string]$_) }
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

try {
  Invoke-Step "swing pick" `
    ".\.venv\Scripts\python.exe" @("scripts\run_swing_pick.py")
  exit 0
} catch {
  $reason = $_.Exception.Message
  Write-Log "[$(Get-Date -Format o)] FAILED: $reason"
  # 파이썬 안에서도 실패 알림을 보내지만, import 단계에서 죽으면 그 코드까지 못 가서 러너도 보낸다.
  # exit code 2 = 파이썬이 이미 실패 알림 보냄 — 러너 중복 알림 스킵 (run_swing_pick.py)
  if ($reason -match "exit code 2$") { Write-Log "runner notify skipped (already notified by python)" } else {
  $message = "[스윙 후보] $target FAILED`n$reason`nlog: $log"
  try {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "batch" "--best-effort" 2>&1 |
      ForEach-Object { Write-Log ([string]$_) }
    $ErrorActionPreference = $previous
  } catch {}
  }
  throw
}
