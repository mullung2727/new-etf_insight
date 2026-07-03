$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# 리포트는 당일 게시 → 당일 KST 기준 수집. collect은 post_id 멱등, download은 파일 존재 스킵이라
# 하루 여러 번(시간단위) 돌려도 안전. 시간단위로 바꾸려면 registry cron만 수정.
$target = (Get-Date).ToString("yyyy-MM-dd")
$log = Join-Path $logDir ("telegram-public-channel-" + (Get-Date).ToString("yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
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
  Invoke-Step "collect telegram public channels + report attachments" `
    ".\.venv\Scripts\python.exe" @("scripts\run_telegram_channels.py", "--date", $target)

  $report = @'
import sqlite3
date = "__DATE__"
db = "db/telegram_public.sqlite3"
con = sqlite3.connect(db)
con.execute("PRAGMA query_only=ON")
rows = con.execute(
    "SELECT channel, COUNT(*) FROM telegram_posts WHERE date_kst=? GROUP BY channel ORDER BY channel",
    [date],
).fetchall()
con.close()
lines = ["[Telegram 공개채널] " + date]
if rows:
    for ch, cnt in rows:
        lines.append(f"- {ch}: {cnt}건 수집")
else:
    lines.append("- 수집 글 없음(해당 일자 게시 없음 가능)")
print("\n".join(lines))
'@.Replace("__DATE__", $target)
  $message = $report | .\.venv\Scripts\python.exe -
  $message | Tee-Object -FilePath $log -Append | Write-Output
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $message, "--channel", "telegram_report")
  exit 0
} catch {
  $message = "[Telegram 공개채널] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
