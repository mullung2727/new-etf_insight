$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# DART 정기보고서 접수 마감은 18:00 → 19:00 실행은 당일치를 그날 받는다.
# 제출이 없는 날은 공시목록 1콜로 끝나므로 매일 돌려도 비용이 없다.
# 실행 시각은 한 번만 읽는다 - 피크일 훑기는 수십 분이라 도중에 자정을 넘길 수 있고,
# 그때 Get-Date 를 다시 부르면 창이 하루 밀려 정작 받으려던 날을 건너뛴다.
$runDate = Get-Date
$target = $runDate.ToString("yyyyMMdd")
$log = Join-Path $logDir ("financial-indicators-" + $target + ".log")

# PC가 꺼져 19:00 트리거를 거르면 그날 제출분이 영영 안 들어온다(다음 실행은 다른 날짜를
# 받으므로). 최근 며칠을 같이 훑어 다음 성공 실행이 주워담게 한다. 적재는 멱등(upsert)이고
# 제출 없는 날은 1콜이라 재방문 비용이 거의 없다.
# ponytail: 3일을 넘겨 꺼져 있었으면 그 날짜로 --filings-on 을 직접 돌려야 한다.
$lookbackDays = 3

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
  $out = @()
  foreach ($offset in ($lookbackDays - 1)..0) {
    $day = $runDate.AddDays(-$offset).ToString("yyyyMMdd")
    $out += Invoke-Step "load DART periodic filings $day" `
      ".\.venv\Scripts\python.exe" @("scripts\build_financial_indicators.py", "--filings-on", $day)
  }

  # 제출사 0이면 알리지 않는다 - 대부분의 날이 여기 해당하고, 매일 오는 "0건" 알림은 소음이다.
  $submitters = 0
  foreach ($line in $out) {
    if ("$line" -match "상장 제출사 (\d+)") { $submitters += [int]$Matches[1] }
  }

  if ($submitters -gt 0) {
    $picked = $out | Where-Object { "$_" -match "상장 제출사|대상 \d+ \(skip|무응답|결산월 불일치|판정 불가" }
    $message = "[재무제표 수집] $target`n" + (($picked | ForEach-Object { "$_" }) -join "`n")
    $message | Tee-Object -FilePath $log -Append | Write-Output
    Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $message, "--channel", "batch")
  } else {
    "[$(Get-Date -Format o)] 정기보고서 제출 0건 - Discord 전송 생략" | Tee-Object -FilePath $log -Append | Write-Output
  }
  exit 0
} catch {
  $message = "[재무제표 수집] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "batch" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
