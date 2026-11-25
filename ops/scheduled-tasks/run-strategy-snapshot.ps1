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
  # 새 전략은 이 배열에 한 줄 추가
  $bts = @(
    @{ Module = "research.private.intraday_rebound.shadow_bt"; Log = "strategy-snapshot-bt-$date.log"; Label = "그림자 백테스트" },
    @{ Module = "research.private.rights_issue.shadow_bt"; Log = "strategy-snapshot-bt-rights-$date.log"; Label = "유무상 그림자 백테스트" },
    @{ Module = "research.private.us_oas.shadow_bt"; Log = "strategy-snapshot-bt-us-$date.log"; Label = "미국 v38 그림자 백테스트" }
  )
  foreach ($bt in $bts) {
    & ".venv\Scripts\python.exe" -m $bt.Module *> (Join-Path $logDir $bt.Log)
    $btCode = $LASTEXITCODE
    if ($btCode -ne 0) {
      & ".venv\Scripts\python.exe" scripts\send_report_messages.py --message "❌ strategy-snapshot $($bt.Label) 실패 (exit $btCode) — etl\logs\$($bt.Log)"
      $code = 1
    }
  }
}
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
