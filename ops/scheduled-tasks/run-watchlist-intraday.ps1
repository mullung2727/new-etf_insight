$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$reportsDir = "C:\Users\mullu\.openclaw\workspace\reports"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$todayCompact = Get-Date -Format "yyyyMMdd"
$todayDash = Get-Date -Format "yyyy-MM-dd"
$log = Join-Path $logDir ("watchlist-intraday-" + $todayCompact + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  $previousErrorActionPreference = $ErrorActionPreference
  $code = 1
  try {
    $ErrorActionPreference = "Continue"
    & $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

function Invoke-BestEffortStep {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  try {
    Invoke-Step -Label $Label -Exe $Exe -StepArgs $StepArgs
  } catch {
    "[$(Get-Date -Format o)] WARNING: $Label skipped: $($_.Exception.Message)" |
      Tee-Object -FilePath $log -Append | Write-Output
  }
}

try {
  "[$(Get-Date -Format o)] precheck KRX trading day" | Tee-Object -FilePath $log -Append | Write-Output
  & ".\.venv\Scripts\python.exe" "scripts\check_krx_trading_day.py" "--date" $todayCompact 2>&1 |
    Tee-Object -FilePath $log -Append | Write-Output
  $calendarCode = $LASTEXITCODE
  if ($LASTEXITCODE -eq 3) {
    "[watchlist intraday] $todayCompact NON_TRADING_DAY - full batch skipped" |
      Tee-Object -FilePath $log -Append | Write-Output
    exit 0
  }
  if ($calendarCode -ne 0) {
    throw "trading-day precheck failed with exit code $calendarCode"
  }

  Invoke-Step "build intraday ranking" ".\.venv\Scripts\python.exe" @("scripts\build_intraday_ranking.py", "--date", $todayCompact)
  Invoke-BestEffortStep "capture 15:00 market snapshot" ".\.venv\Scripts\python.exe" @("scripts\collect_watchlist_market_snapshot.py", "--date", $todayCompact)
  Invoke-Step "D+1 open probability scoring" ".\.venv\Scripts\python.exe" @(
    "..\research\watchlist_expected_return\watchlist_probability_langgraph.py",
    "--dates", $todayCompact,
    "--write-db",
    "--reports-dir", $reportsDir
  )

  $researchJson = Join-Path $reportsDir ("watchlist_research_" + $todayDash + ".json")
  $discordJson = Join-Path $reportsDir ("watchlist_discord_" + $todayDash + ".json")
  Invoke-Step "format Discord report" ".\.venv\Scripts\python.exe" @(
    "-X", "utf8",
    "scripts\format_watchlist_discord_report.py",
    "--json", $researchJson,
    "--db", (Join-Path $etlDir "db\watchlist.sqlite3"),
    "--out", $discordJson,
    "--fail-on-mojibake"
  )
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--json", $discordJson)
  exit 0
} catch {
  $message = "[watchlist intraday] $todayCompact FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
