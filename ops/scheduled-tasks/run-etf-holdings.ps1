$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$now = Get-Date
$today = $now.ToString("yyyyMMdd")
$log = Join-Path $logDir ("etf-holdings-" + $today + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Out-Null
  $eap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $out = & $Exe @StepArgs 2>&1 | ForEach-Object { "$_" }
  $code = $LASTEXITCODE
  $ErrorActionPreference = $eap
  $out | Tee-Object -FilePath $log -Append | Out-Null
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
  return ($out -join "`n")
}

try {
  Invoke-Step "build ETF holdings daily" ".\.venv\Scripts\python.exe" @("scripts\build_etf_holdings.py", "--daily") | Out-Null
  exit 0
} catch {
  $message = "[ETF보유] $today FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--best-effort" | Tee-Object -FilePath $log -Append | Out-Null
  } catch {}
  throw
}
