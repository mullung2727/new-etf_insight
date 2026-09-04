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
$OutputEncoding = New-Object System.Text.UTF8Encoding $false  # BOM 없이 — here-string을 python - 로 파이프할 때 stdin U+FEFF 오염 방지
$env:PYTHONUTF8 = "1"

# Tee-Object 는 PS5.1 에서 -Encoding 을 받지 않아 UTF-16LE 로 쓴다. UTF-8 로 직접 붙인다.
$logEncoding = New-Object System.Text.UTF8Encoding $false
function Write-Log {
  param([string]$Text)
  # AppendAllText 실패는 .NET 예외라 EAP 와 무관하게 종료성이다. 로그를 못 써서
  # 실패 알림까지 막히면 배치가 조용히 죽는다. 기록 실패는 삼킨다.
  try { [System.IO.File]::AppendAllText($log, $Text + [Environment]::NewLine, $logEncoding) } catch {}
  Write-Output $Text
}

function Invoke-Step {
  param([string]$Label, [string]$Exe, [string[]]$StepArgs)
  Write-Log "[$(Get-Date -Format o)] $Label"
  # EAP=Stop 이면 네이티브 명령의 stderr 가 NativeCommandError 로 던져져, Tee/로그가 한 줄도
  # 남기지 못한 채 catch 로 튄다. 이 구간만 Continue 로 내려 stderr 를 로그에 남긴다.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Exe @StepArgs 2>&1 | ForEach-Object { Write-Log ([string]$_) }
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
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

  # 2026-09-04 사건: 이 파일이 무BOM 이라 PS5.1 이 CP949 로 읽어 위 here-string 의 한글이
  # 깨졌고, 깨진 소스를 받은 인터프리터가 SyntaxError 로 죽어 $message 가 $null 이 됐다.
  # 그런데 여기엔 stderr 리다이렉션이 없어 그 SyntaxError 가 어디에도 남지 않았다.
  # 스트림을 나눠 받아 stdout 만 메시지로 쓰고 stderr 는 로그로 보낸다.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $raw = $report | .\.venv\Scripts\python.exe - 2>&1
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
  $stdout = @()
  foreach ($line in $raw) {
    if ($line -is [System.Management.Automation.ErrorRecord]) { Write-Log ([string]$line) }
    else { $stdout += [string]$line }
  }
  if ($code -ne 0) {
    throw "build report message failed with exit code $code"
  }
  $message = $stdout -join "`n"
  if (-not $message) {
    throw "build report message produced no output"
  }
  Write-Log $message
  Invoke-Step "send Discord report" ".\.venv\Scripts\python.exe" @("scripts\send_report_messages.py", "--message", $message, "--channel", "telegram_report")
  exit 0
} catch {
  $reason = $_.Exception.Message
  Write-Log "[$(Get-Date -Format o)] FAILED: $reason"
  $message = "[증권사 종목리포트] $target FAILED`n$reason`nlog: $log"
  try {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & ".\.venv\Scripts\python.exe" "scripts\send_report_messages.py" "--message" $message "--channel" "telegram_report" "--best-effort" 2>&1 |
      ForEach-Object { Write-Log ([string]$_) }
    $ErrorActionPreference = $previous
  } catch {}
  throw
}
