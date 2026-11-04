$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# 스케줄: 평일 22:00(당일분) · 화~토 07:00/08:00/08:30(전일분). 매 실행은 최근 7일 중 빠진 날만 받는다(이미 받은 날·확정 휴장일은 건너뜀).
# 2026-09-29: 08:00 단발 KRX read timeout 으로 전일분이 빠졌는데, 기존엔 "어제 하루"만 받아 다음날에도 재시도되지 않았다.
$now = Get-Date
$today = $now.ToString("yyyyMMdd")
$from = $now.AddDays(-7).ToString("yyyyMMdd")
$yday = $now.AddDays(-1).ToString("yyyyMMdd")
$isFinal = ($now.Hour -eq 8 -and $now.Minute -ge 25) -or ($now.Hour -ge 9 -and $now.Hour -lt 12)   # 아침 마지막 시도(08:30)
$log = Join-Path $logDir ("krx-ohlcv-" + $today + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false  # BOM 없이 — here-string을 python - 로 파이프할 때 stdin U+FEFF 오염 방지
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Out-Null
  $eap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"   # native stderr(경고)가 Stop 에 걸려 배치를 죽이지 않게
  $out = & $Exe @StepArgs 2>&1 | ForEach-Object { "$_" }
  $code = $LASTEXITCODE
  $ErrorActionPreference = $eap
  $out | Tee-Object -FilePath $log -Append | Out-Null
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
  return ($out -join "`n")
}

function Send-Report([string]$Message) {
  $Message | Tee-Object -FilePath $log -Append | Out-Null
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $Message) | Out-Null
}

try {
  if ($now.Hour -eq 7) {
    "[$(Get-Date -Format o)] minute daily" | Tee-Object -FilePath $log -Append | Out-Null
    $eapMd = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $mdOut = & ".\.venv\Scripts\python.exe" @("scripts\build_krx_from_minute.py") 2>&1 | ForEach-Object { "$_" }
    $mdCode = $LASTEXITCODE
    $ErrorActionPreference = $eapMd
    $mdOut | Tee-Object -FilePath $log -Append | Out-Null
    "minute-daily exit=$mdCode" | Tee-Object -FilePath $log -Append | Out-Null
    $mdText = ($mdOut -join "`n")
    if ($mdText -match "MINUTE_DAILY status=loaded date=(\d+) rows=(\d+) skipped_corp=(\d+)") {
      Send-Report ("[KRX OHLCV] " + $Matches[1] + " 분봉 일봉 선적재 " + $Matches[2] + "종목 (기업행위 의심 제외 " + $Matches[3] + ") — KRX 오면 대조·교정")
    } elseif ($mdText -match "MINUTE_DAILY status=(no_minute|minute_locked)") {
      Send-Report ("❌ [KRX OHLCV] 분봉 일봉 실패(" + $Matches[1] + ") — 08:00 KRX 대기")
    } elseif ($mdCode -ne 0 -and $mdText -notmatch "MINUTE_DAILY status=") {
      Send-Report ("❌ [KRX OHLCV] 분봉 일봉 실패(exit " + $mdCode + ") — 08:00 KRX 대기`nlog: " + $log)
    }
  }
  $build = Invoke-Step "build KRX OHLCV $from~$today" ".\.venv\Scripts\python.exe" @("scripts\build_krx_ohlcv.py", "--from", $from, "--to", $today)
  $fetched = 0
  if ($build -match "fetched=(\d+)d") { $fetched = [int]$Matches[1] }
  $recBlocks = [regex]::Matches($build, '(?s)RECONCILE_BEGIN\r?\n(.*?)\r?\nRECONCILE_END')
  foreach ($m in $recBlocks) {
    Send-Report ($m.Groups[1].Value.Trim())
  }

  $status = @'
import duckdb
from new_etf_insight.data_paths import db_dir
yday = "__YDAY__"
con = duckdb.connect(str(db_dir() / "krx_ohlcv.duckdb"), read_only=True)
latest = con.execute("SELECT max(date) FROM ohlcv").fetchone()[0]
cnt, min_t, max_t, total_tv = con.execute(
    "SELECT COUNT(*), MIN(ticker), MAX(ticker), SUM(trading_value) FROM ohlcv WHERE date=?", [latest]).fetchone()
has_yday = con.execute("SELECT COUNT(*) FROM ohlcv WHERE date=?", [yday]).fetchone()[0] > 0
try:
    is_prov = con.execute("SELECT COUNT(*) FROM ohlcv_provisional WHERE date=?", [yday]).fetchone()[0] > 0
except Exception:
    is_prov = False
con.close()
print(latest)
print("1" if has_yday else "0")
print("1" if is_prov else "0")
print(
    "[KRX OHLCV] " + latest + "\n"
    + f"- rows: {cnt}\n"
    + f"- ticker range: {min_t}~{max_t}\n"
    + f"- total trading value: {(total_tv or 0):,.0f}\n"
    + "- DB: " + str(db_dir() / "krx_ohlcv.duckdb") + "\n"
    + "- watchlist build / LLM scoring: skipped by design"
)
'@.Replace("__YDAY__", $yday)
  $lines = @($status | .\.venv\Scripts\python.exe -)
  if ($LASTEXITCODE -ne 0) { throw "status query failed with exit code $LASTEXITCODE" }
  $latest, $hasYday, $isProv = $lines[0], $lines[1], $lines[2]
  $report = ($lines | Select-Object -Skip 3) -join "`n"
  "fetched=$fetched latest=$latest has_yday=$hasYday is_prov=$isProv final=$isFinal" | Tee-Object -FilePath $log -Append | Out-Null

  if ($fetched -gt 0) {
    Send-Report $report
  }
  # 성공 보고와 따로 판단 — 옛 누락일을 채운 실행이어도 전일분이 없으면 경고해야 한다 (CodeRabbit PR #34)
  if ($isFinal -and $now.AddDays(-1).DayOfWeek -notin @("Saturday", "Sunday")) {
    if ($hasYday -ne "1") {
      # 마지막 시도까지 전일분 없음 — 휴장이면 무시해도 되고, 아니면 수동 적재 필요
      $tail = ($build -split "`n" | Where-Object { $_ -match "fetch failed|KRX empty|retry" }) -join "`n"
      Send-Report "[KRX OHLCV] $yday 일봉 없음 (08:30 마지막 시도) — 휴장이 아니면 수동 적재 필요`n최신 적재일 $latest`n$tail`nlog: $log"
    } elseif ($isProv -eq "1") {
      Send-Report "⚠ [KRX OHLCV] $yday KRX 미도착 — 분봉 일봉으로 운영 중, 22:00·내일 재시도"
    }
  }
  exit 0
} catch {
  $message = "[KRX OHLCV] $today FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Out-Null
  } catch {}
  throw
}
