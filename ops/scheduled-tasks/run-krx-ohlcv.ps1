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
  $build = Invoke-Step "build KRX OHLCV $from~$today" ".\.venv\Scripts\python.exe" @("scripts\build_krx_ohlcv.py", "--from", $from, "--to", $today)
  $fetched = 0
  if ($build -match "fetched=(\d+)d") { $fetched = [int]$Matches[1] }

  $status = @'
import duckdb
yday = "__YDAY__"
con = duckdb.connect("db/krx_ohlcv.duckdb", read_only=True)
latest = con.execute("SELECT max(date) FROM ohlcv").fetchone()[0]
cnt, min_t, max_t, total_tv = con.execute(
    "SELECT COUNT(*), MIN(ticker), MAX(ticker), SUM(trading_value) FROM ohlcv WHERE date=?", [latest]).fetchone()
con.close()
print(latest)
print("1" if latest >= yday else "0")
print(
    "[KRX OHLCV] " + latest + "\n"
    + f"- rows: {cnt}\n"
    + f"- ticker range: {min_t}~{max_t}\n"
    + f"- total trading value: {(total_tv or 0):,.0f}\n"
    + "- DB: db/krx_ohlcv.duckdb\n"
    + "- watchlist build / LLM scoring: skipped by design"
)
'@.Replace("__YDAY__", $yday)
  $lines = @($status | .\.venv\Scripts\python.exe -)
  $latest, $hasYday = $lines[0], $lines[1]
  $report = ($lines | Select-Object -Skip 2) -join "`n"
  "fetched=$fetched latest=$latest has_yday=$hasYday final=$isFinal" | Tee-Object -FilePath $log -Append | Out-Null

  if ($fetched -gt 0) {
    Send-Report $report
  }
  # 성공 보고와 따로 판단 — 옛 누락일을 채운 실행이어도 전일분이 없으면 경고해야 한다 (CodeRabbit PR #34)
  if ($isFinal -and $hasYday -ne "1" -and $now.AddDays(-1).DayOfWeek -notin @("Saturday", "Sunday")) {
    # 마지막 시도까지 전일분 없음 — 휴장이면 무시해도 되고, 아니면 수동 적재 필요
    $tail = ($build -split "`n" | Where-Object { $_ -match "fetch failed|KRX empty|retry" }) -join "`n"
    Send-Report "[KRX OHLCV] $yday 일봉 없음 (08:30 마지막 시도) — 휴장이 아니면 수동 적재 필요`n최신 적재일 $latest`n$tail`nlog: $log"
  }
  exit 0
} catch {
  $message = "[KRX OHLCV] $today FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Out-Null
  } catch {}
  throw
}
