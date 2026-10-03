param(
  [ValidateRange(1, 240)]
  [int]$MaxRuntimeMin = 210,
  [ValidateRange(0, 240)]
  [int]$NxtRuntimeMin = 30
)

$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$dateKey = Get-Date -Format "yyyyMMdd"
$log = Join-Path $logDir ("minute-backfill-" + $dateKey + ".log")
$errorLog = Join-Path $logDir ("minute-backfill-" + $dateKey + ".error.log")
$report = Join-Path $logDir ("minute-backfill-" + $dateKey + ".json")
$nxtLog = Join-Path $logDir ("minute-backfill-" + $dateKey + "-nxt.log")
$nxtErrorLog = Join-Path $logDir ("minute-backfill-" + $dateKey + "-nxt.error.log")
$nxtReport = Join-Path $logDir ("minute-backfill-" + $dateKey + "-nxt.json")
$mutex = New-Object System.Threading.Mutex($false, "Global\new-etf-insight-minute-backfill")

if (-not $mutex.WaitOne(0)) {
  "[$(Get-Date -Format o)] already running; skip" | Tee-Object -FilePath $log -Append
  $mutex.Dispose()
  exit 0
}

try {
  Set-Location $etlDir
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $env:PYTHONUTF8 = "1"
  $env:KIWOOM_MIN_INTERVAL = "0.5"
  $env:MINUTE_DB_THREADS = "1"
  $env:MINUTE_DB_MEMORY_LIMIT = "1GB"

  # NXT 패스(시간외 15:40~20:00)를 먼저 작은 예산으로 수집한다. 실패해도 KRX 패스는 계속한다.
  $nxtArgs = @(
    "scripts\backfill_minute_bars.py",
    "--market", "nxt",
    "--months-back", "12",
    "--max-runtime-min", $NxtRuntimeMin.ToString(),
    "--api-interval", "0.5",
    "--max-attempts", "3",
    "--max-failures", "20",
    "--report-file", $nxtReport
  )
  $nxtData = $null
  $nxtError = $null
  try {
    $nxtProcess = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList $nxtArgs `
      -WorkingDirectory $etlDir -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput $nxtLog -RedirectStandardError $nxtErrorLog
    try { $nxtProcess.PriorityClass = "BelowNormal" } catch {}
    Wait-Process -InputObject $nxtProcess
    if ($nxtProcess.ExitCode -ne 0) {
      throw "minute backfill (nxt) failed with exit code $($nxtProcess.ExitCode)"
    }
    $nxtData = Get-Content -LiteralPath $nxtReport -Raw -Encoding UTF8 | ConvertFrom-Json
  } catch {
    $nxtError = $_.Exception.Message
    "[$(Get-Date -Format o)] NXT pass failed; continue to KRX: $nxtError" | Tee-Object -FilePath $nxtLog -Append
  }

  $args = @(
    "scripts\backfill_minute_bars.py",
    "--months-back", "12",
    "--max-runtime-min", ($MaxRuntimeMin - $NxtRuntimeMin).ToString(),
    "--api-interval", "0.5",
    "--max-attempts", "3",
    "--max-failures", "20",
    "--report-file", $report
  )
  $process = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList $args `
    -WorkingDirectory $etlDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError $errorLog
  try { $process.PriorityClass = "BelowNormal" } catch {}
  # PS 5.1에서 리디렉션한 Start-Process 객체는 .WaitForExit() 뒤 ExitCode가 $null일 수 있다.
  # Wait-Process는 우선순위를 먼저 낮춘 채 기다리면서 실제 종료코드를 보존한다.
  Wait-Process -InputObject $process
  if ($process.ExitCode -ne 0) {
    throw "minute backfill failed with exit code $($process.ExitCode)"
  }

  $data = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
  $lines = @("[1분봉 백필] $dateKey 완료 · KRX $($data.latest_krx_date)")
  foreach ($item in $data.results) {
    $lines += ("- {0}: 종목 {1}/{2}, 신규봉 {3:N0}, 남은 종목일 {4:N0}, 실패 {5}, 보류 {6}, 종료 {7}" -f `
      $item.month, $item.completed_tickers, $item.attempted_tickers, $item.inserted_bars, `
      $item.remaining_ticker_days, $item.failed_tickers, $item.blocked_total, $item.stop_reason)
  }
  if ($nxtError) {
    $lines += "- NXT 실패: $nxtError (KRX는 정상 진행)"
  } else {
    foreach ($item in $nxtData.results) {
      $lines += ("- NXT {0}: 종목 {1}/{2}, 신규봉 {3:N0}, 남은 종목일 {4:N0}, 실패 {5}, 보류 {6}, 종료 {7}" -f `
        $item.month, $item.completed_tickers, $item.attempted_tickers, $item.inserted_bars, `
        $item.remaining_ticker_days, $item.failed_tickers, $item.blocked_total, $item.stop_reason)
    }
  }
  $message = $lines -join "`n"
  & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort"
  exit 0
} catch {
  $message = "[1분봉 백필] $dateKey 실패`n$($_.Exception.Message)`nlog: $log`nerror: $errorLog"
  try {
    Set-Location $etlDir
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort"
  } catch {}
  throw
} finally {
  try { $mutex.ReleaseMutex() } catch {}
  $mutex.Dispose()
}
