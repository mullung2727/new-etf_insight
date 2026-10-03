param(
  [Parameter(Mandatory)][string]$From,
  [Parameter(Mandatory)][string]$To,
  [string]$Deadline = "01:50"
)

$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$log = Join-Path $logDir ("krx-ohlcv-refetch-" + $From + "-" + $To + ".log")
$errorLog = Join-Path $logDir ("krx-ohlcv-refetch-" + $From + "-" + $To + ".error.log")
$mutex = New-Object System.Threading.Mutex($false, "Global\new-etf-insight-krx-ohlcv-refetch")

if (-not $mutex.WaitOne(0)) {
  "[$(Get-Date -Format o)] already running; skip" | Tee-Object -FilePath $log -Append
  $mutex.Dispose()
  exit 0
}

try {
  Set-Location $etlDir
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $env:PYTHONUTF8 = "1"

  $parts = $Deadline -split ":"
  $deadlineTime = Get-Date -Hour ([int]$parts[0]) -Minute ([int]$parts[1]) -Second 0
  if ((Get-Date) -gt $deadlineTime) {
    $deadlineTime = $deadlineTime.AddDays(1)
  }
  $remainingSec = [int]($deadlineTime - (Get-Date)).TotalSeconds
  if ($remainingSec -le 0) {
    throw "데드라인 도달로 중단: 남은 시간 없음 (deadline $Deadline)"
  }
  $remainingSec = [Math]::Min($remainingSec, 32767)

  $args = @(
    "scripts\build_krx_ohlcv.py",
    "--from", $From,
    "--to", $To,
    "--force"
  )
  $process = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList $args `
    -WorkingDirectory $etlDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError $errorLog
  try { $process.PriorityClass = "BelowNormal" } catch {}
  # PS 5.1에서 리디렉션한 Start-Process 객체는 .WaitForExit() 뒤 ExitCode가 $null일 수 있다.
  # Wait-Process는 우선순위를 먼저 낮춘 채 기다리면서 실제 종료코드를 보존한다.
  Wait-Process -InputObject $process -Timeout $remainingSec -ErrorAction SilentlyContinue
  $process.Refresh()
  if (-not $process.HasExited) {
    try { Stop-Process -Id $process.Id -Force } catch {}
    throw "데드라인 도달로 중단 (deadline $Deadline)"
  }
  if ($process.ExitCode -ne 0) {
    throw "krx ohlcv refetch failed with exit code $($process.ExitCode)"
  }

  $lastLine = ""
  if (Test-Path -LiteralPath $log) {
    $tail = Get-Content -LiteralPath $log -Encoding UTF8 | Select-Object -Last 1
    if ($null -ne $tail) { $lastLine = $tail }
  }
  $failCount = 0
  if (Test-Path -LiteralPath $log) {
    $failCount = @(Select-String -LiteralPath $log -Pattern "fetch failed" -SimpleMatch -ErrorAction SilentlyContinue).Count
  }
  $lines = @("[KRX 일봉 재수집] $From~$To 완료")
  if ($lastLine -ne "") { $lines += $lastLine }
  if ($failCount -gt 0) { $lines += ("실패일 {0}" -f $failCount) }
  $message = $lines -join "`n"
  & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort"
  exit 0
} catch {
  $message = "[KRX 일봉 재수집] $From~$To 실패`n$($_.Exception.Message)`nlog: $log`nerror: $errorLog"
  try {
    Set-Location $etlDir
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort"
  } catch {}
  throw
} finally {
  try { $mutex.ReleaseMutex() } catch {}
  $mutex.Dispose()
}
