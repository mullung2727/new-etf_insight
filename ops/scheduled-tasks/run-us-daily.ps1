$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

$target = (Get-Date).ToString("yyyyMMdd")
$log = Join-Path $logDir ("us-daily-" + $target + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false  # no BOM, keeps piped stdin clean for python
$env:PYTHONUTF8 = "1"

$script:lastCode = 0
$script:lastOutput = @()

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  # Native stderr (e.g. yfinance FutureWarning) must not kill the session:
  # relax the preference only around the native call, judge by exit code only.
  $prevAction = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $script:lastOutput = @(& $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append | ForEach-Object { "$_" })
  } finally {
    $ErrorActionPreference = $prevAction
  }
  $script:lastCode = $LASTEXITCODE
  $script:lastOutput | Write-Output
}

function Send-FailureReport {
  param([string]$Message)
  $prevAction = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $Message "--best-effort" 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  } catch {
  } finally {
    $ErrorActionPreference = $prevAction
  }
}

try {
  $failedSteps = @()
  $oasLines = @()

  Invoke-Step "build US OHLCV" ".\.venv\Scripts\python.exe" @("scripts\build_us_ohlcv.py")
  $ohlcvSummary = @($script:lastOutput | Select-String "universe=" | ForEach-Object { $_.Line } | Select-Object -Last 1)
  if ($script:lastCode -ne 0) { $failedSteps += "build_us_ohlcv.py (exit $($script:lastCode))" }

  # Macro is a 3-year rolling fetch: run it even if step 1 failed.
  Invoke-Step "build US macro" ".\.venv\Scripts\python.exe" @("scripts\build_us_macro.py")
  $macroLines = @($script:lastOutput | Where-Object { ($_ -match " FAILED: ") -or (($_ -match "^[^ ]+=[^ ]+$") -and ($_ -notmatch "=0$")) })
  if ($script:lastCode -ne 0) { $failedSteps += "build_us_macro.py (exit $($script:lastCode))" }

  Invoke-Step "OAS spike alert" ".\.venv\Scripts\python.exe" @("scripts\us_oas_alert.py")
  $oasLines = @($script:lastOutput | Where-Object { $_ -match "^OAS " })
  $oasDate = ""
  foreach ($line in $oasLines) {
    if ($line -match "^OAS (\d{8}) est ") { $oasDate = $Matches[1]; break }
  }
  if ($script:lastCode -ne 0) { $failedSteps += "us_oas_alert.py (exit $($script:lastCode))" }

  if ($failedSteps.Count -eq 0) {
    if ($ohlcvSummary.Count -eq 0) { $ohlcvSummary = @("(no summary line)") }
    $reportLines = @("[US DAILY] " + $target)
    $reportLines += @($oasLines | Where-Object { $_ -match "^OAS ALERT" })
    $reportLines += @("- build_us_ohlcv: " + $ohlcvSummary[0])
    if ($macroLines.Count -gt 0) {
      $reportLines += "- build_us_macro:"
      $reportLines += ($macroLines | ForEach-Object { "  " + $_ })
    } else {
      $reportLines += "- build_us_macro: no new rows"
    }
    $reportLines += @($oasLines | Where-Object { $_ -notmatch "^OAS ALERT" } | ForEach-Object { "- " + $_ })
    $message = $reportLines -join "`n"
    $message | Tee-Object -FilePath $log -Append | Write-Output
    Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $message)
    if ($script:lastCode -ne 0) { throw "send Discord report failed with exit code $($script:lastCode)" }
    if ($oasDate -ne "") {
      Invoke-Step "OAS mark reported" ".\.venv\Scripts\python.exe" @("scripts\us_oas_alert.py", "--mark-reported", $oasDate)
      if ($script:lastCode -ne 0) { "WARN: OAS mark reported failed (exit $($script:lastCode))" | Tee-Object -FilePath $log -Append | Write-Output }
    }
    exit 0
  } else {
    $message = "[US DAILY] " + $target + " FAILED`n" + ($failedSteps -join "`n") + "`nlog: " + $log
    if ($oasLines.Count -gt 0) { $message += "`n" + ($oasLines -join "`n") }
    $message | Tee-Object -FilePath $log -Append | Write-Output
    Send-FailureReport $message
    exit 1
  }
} catch {
  $message = "[US DAILY] " + $target + " FAILED`n$($_.Exception.Message)`nlog: " + $log
  $message | Tee-Object -FilePath $log -Append | Write-Output
  Send-FailureReport $message
  exit 1
}
