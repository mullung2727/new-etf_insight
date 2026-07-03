$ErrorActionPreference = "Stop"
$projectRoot = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$etlDir = Join-Path $projectRoot "etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null

# 종목 리포트는 당일 발행 → 당일 KST 기준. 멱등(파일 존재 스킵)이라 하루 여러 번 돌려도 안전.
$target = (Get-Date).ToString("yyyy-MM-dd")
$log = Join-Path $logDir ("naver-research-" + (Get-Date).ToString("yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$Args)
  "[$(Get-Date -Format o)] $Label" | Tee-Object -FilePath $log -Append | Write-Output
  & $Exe @Args 2>&1 | Tee-Object -FilePath $log -Append | Write-Output
  $code = $LASTEXITCODE
  if ($code -ne 0) {
    throw "$Label failed with exit code $code"
  }
}

try {
  Invoke-Step "download naver research reports" `
    ".\.venv\Scripts\python.exe" @("scripts\download_naver_research.py", "--date", $target)

  $report = @'
import pathlib
date = "__DATE__"
base = pathlib.Path("exports/stock_reports")
pdfs = sorted(base.rglob(date + "_*.pdf")) if base.exists() else []
stocks = sorted({p.parent.name for p in pdfs})
lines = ["[증권사 종목리포트] " + date, f"- PDF: {len(pdfs)}건"]
for s in stocks[:40]:
    lines.append("  - " + s)
if len(stocks) > 40:
    lines.append(f"  ... 외 {len(stocks) - 40}종목")
print("\n".join(lines))
'@.Replace("__DATE__", $target)
  $message = $report | .\.venv\Scripts\python.exe -
  $message | Tee-Object -FilePath $log -Append | Write-Output
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $message, "--channel", "telegram_report")
  exit 0
} catch {
  $message = "[증권사 종목리포트] $target FAILED`n$($_.Exception.Message)`nlog: $log"
  try {
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" | Tee-Object -FilePath $log -Append | Write-Output
  } catch {}
  throw
}
