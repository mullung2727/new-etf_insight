$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$rlVersion = "v2"
$rlStart = "20261109"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
. (Join-Path $PSScriptRoot "lib_trading_day_guard.ps1")
Exit-IfNonTradingDay -Tag "rl-signal-train"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
# research.* lives at the repo root; .venv resolves from etl (cwd).
$env:PYTHONPATH = ".."

# 야간 재학습·다음날 평가 (주문 없음): 증분 실행 후 디스코드로 결과 알림.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.rl_signal.walkforward `
  --version $rlVersion --model hgb_adv --incremental --shuffle-reps 20 `
  *> (Join-Path $logDir "rl-signal-train-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -eq 0) {
  & ".venv\Scripts\python.exe" -m research.rl_signal.report --version $rlVersion --model hgb_adv --day $date --start $rlStart
} else {
  & ".venv\Scripts\python.exe" -m research.rl_signal.report --version $rlVersion --model hgb_adv --day $date --start $rlStart --failed "walkforward exit $code"
  exit 1
}
exit 0
