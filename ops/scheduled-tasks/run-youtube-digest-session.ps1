param(
  [ValidateSet("morning", "evening")]
  [string]$Session = "morning"
)
$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# ASCII-only file on purpose: PS 5.1 reads a no-BOM UTF-8 .ps1 as system ANSI (ks_c_5601)
# and mangles non-ascii literals (see run-youtube-daily.ps1).
#
# Twice-daily market digest: morning 10:00 KST, evening 18:00 KST.
#   collect (RSS + transcript) -> per-video auto summary (existing) -> cross-video digest.
# Both sessions run on the current KST day. morning also collects the previous day so a
# video published after last night's 01:00 batch is present before the digest runs.
$target = (Get-Date).ToString("yyyy-MM-dd")
$log = Join-Path $logDir ("youtube-digest-" + (Get-Date).ToString("yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  # $ErrorActionPreference=Stop + `& native 2>&1` promotes a single native stderr line
  # (e.g. the codex startup banner) to a terminating error even on exit 0. Judge success
  # by $LASTEXITCODE only inside this call.
  $prevEAP = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  & $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prevEAP
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

# Steps 1-2 feed the digest but must not block it: YouTube's RSS backend returns random
# 404/500 for valid feeds, and a dead collect step used to abort the run before anything
# was delivered. Collect/summarize failures are demoted to a warning line inside the
# digest message; only the digest step itself decides the exit code.
function Invoke-StepSoft {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  $prevEAP = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  $out = & $Exe @StepArgs 2>&1 | Tee-Object -FilePath $log -Append
  $code = $LASTEXITCODE
  $ErrorActionPreference = $prevEAP
  if ($code -ne 0) {
    "[$(Get-Date -Format o)] $Label exit=$code (continuing)" | Tee-Object -FilePath $log -Append | Write-Output
  }
  # -Width: without it Out-String wraps at the host width (80 in a scheduled task) and can
  # split "ERROR channel=UC..." across lines, silently emptying --collect-failed.
  return @{ Code = $code; Text = ($out | Out-String -Width 500) }
}

try {
  $collectFailed = @()

  # 1) Collect all channels (RSS + transcript). Idempotent per (channel, video).
  $collectSteps = @()
  if ($Session -eq "morning") {
    $collectSteps += (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
  }
  $collectSteps += $target
  foreach ($day in $collectSteps) {
    $r = Invoke-StepSoft "youtube collect all channels ($day)" `
      ".\.venv\Scripts\python.exe" @("scripts\run_youtube_channels.py", "--date", $day)
    foreach ($m in [regex]::Matches($r.Text, 'ERROR channel=(UC[\w-]{22})')) {
      $collectFailed += $m.Groups[1].Value
    }
  }
  $collectFailed = $collectFailed | Sort-Object -Unique

  # 2) Per-video auto summary for summary_mode=auto channels, pending only (lookback 1d).
  $summarize = Invoke-StepSoft "youtube auto summarize ($target lookback 1)" `
    ".\.venv\Scripts\python.exe" @(
      "scripts\run_youtube_analysis.py",
      "--date", $target,
      "--auto-only",
      "--lookback-days", "1"
    )

  # 3) Cross-video digest: unsent summaries within 48h -> one LLM call -> Discord.
  $digestArgs = @(
    "scripts\youtube_digest.py",
    "--date", $target,
    "--session", $Session,
    "--channel", "telegram_report"
  )
  if ($collectFailed.Count -gt 0) {
    $digestArgs += @("--collect-failed", ($collectFailed -join ","))
  }
  if ($summarize.Code -ne 0) {
    $digestArgs += "--summarize-failed"
  }
  Invoke-Step "youtube market digest ($target/$Session)" ".\.venv\Scripts\python.exe" $digestArgs
  exit 0
} catch {
  $message = "[youtube-digest] $target/$Session FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
