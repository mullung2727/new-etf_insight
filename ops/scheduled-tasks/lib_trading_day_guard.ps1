# Shared KRX trading-day pre-check for live-trading scheduled runners.
# Dot-source this file, then call Exit-IfNonTradingDay -Tag "<runner-tag>".
# - Trading day (checker exit 0): returns silently, touches nothing.
# - Non-trading day (checker exit 3): appends a skip line to etl\logs\<Tag>-<date>.log and exits 0.
# - Checker failure (any other non-zero exit): logs and exits with that code (fail closed, no trading).
$script:TradingGuardRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Exit-IfNonTradingDay {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Tag
  )

  $root = $script:TradingGuardRoot
  $date = Get-Date -Format "yyyyMMdd"
  $py = Join-Path $root "etl\.venv\Scripts\python.exe"
  $chk = Join-Path $root "etl\scripts\check_krx_trading_day.py"

  $prevErr = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $out = & $py $chk --date $date 2>&1 | Out-String
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prevErr

  $log = Join-Path $root ("etl\logs\" + $Tag + "-" + $date + ".log")
  $logDir = Split-Path -Parent $log
  if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -Force -ItemType Directory $logDir | Out-Null
  }

  if ($code -eq 3) {
    $msg = "[$Tag] $date NON_TRADING_DAY - skipped. $($out.Trim())"
    $msg | Out-File -Encoding utf8 -Append -FilePath $log
    Write-Output $msg
    exit 0
  }

  if ($code -ne 0) {
    $msg = "[$Tag] $date trading-day check failed (exit $code). $($out.Trim())"
    $msg | Out-File -Encoding utf8 -Append -FilePath $log
    Write-Output $msg
    exit $code
  }
}
