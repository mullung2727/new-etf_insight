$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
# research.private.* lives at the repo root; scripts.* resolves from etl (cwd).
$env:PYTHONPATH = ".."
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m scripts.strategy_snapshot *> (Join-Path $logDir "strategy-snapshot-$date.log")
$code = $LASTEXITCODE
if ($code -ne 0) {
  & ".venv\Scripts\python.exe" scripts\send_report_messages.py --message "❌ strategy-snapshot 실패 (exit $code) — etl\logs\strategy-snapshot-$date.log"
}
# bt 는 live 행(전날 평가액·입출금)에 기대므로 스냅샷 성공 뒤에만
if ($code -eq 0) {
  & ".venv\Scripts\python.exe" -m research.private.intraday_rebound.shadow_bt *> (Join-Path $logDir "strategy-snapshot-bt-$date.log")
  $btCode = $LASTEXITCODE
  if ($btCode -ne 0) {
    & ".venv\Scripts\python.exe" scripts\send_report_messages.py --message "❌ strategy-snapshot 그림자 백테스트 실패 (exit $btCode) — etl\logs\strategy-snapshot-bt-$date.log"
    $code = 1
  }
  & ".venv\Scripts\python.exe" -m research.private.rights_issue.shadow_bt *> (Join-Path $logDir "strategy-snapshot-bt-rights-$date.log")
  $btRightsCode = $LASTEXITCODE
  if ($btRightsCode -ne 0) {
    & ".venv\Scripts\python.exe" scripts\send_report_messages.py --message "❌ strategy-snapshot 유무상 그림자 백테스트 실패 (exit $btRightsCode) — etl\logs\strategy-snapshot-bt-rights-$date.log"
    $code = 1
  }
}
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
