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

# 불완전 파일은 캐시 키(크기·mtime 포함) 덕에 재보관 시 다시 계산됨.
$ErrorActionPreference = "Continue"
$obDir = (& ".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, 'src'); from new_etf_insight.data_paths import orderbook_dir; print(orderbook_dir())" 2>&1 | Out-String).Trim()
$obYyyy = $date.Substring(0, 4)
$obMm = $date.Substring(4, 2)
$tradeOk = Test-Path -LiteralPath (Join-Path $obDir "$obYyyy\$obMm\trade_$date.parquet") -PathType Leaf
$bookOk = Test-Path -LiteralPath (Join-Path $obDir "$obYyyy\$obMm\book_$date.parquet") -PathType Leaf
if (-not $tradeOk -or -not $bookOk) {
  & ".venv\Scripts\python.exe" -m research.rl_signal.report --version $rlVersion --model hgb_adv --day $date --start $rlStart --failed "호가 보관 파일 없음"
  exit 1
}

# 야간 재학습·다음날 평가 (주문 없음): 증분 실행 후 디스코드로 결과 알림.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" -m research.rl_signal.walkforward `
  --version $rlVersion --model hgb_adv --incremental --shuffle-reps 20 `
  *> (Join-Path $logDir "rl-signal-train-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -eq 0) {
  & ".venv\Scripts\python.exe" -m research.rl_signal.report --version $rlVersion --model hgb_adv --day $date --start $rlStart
  if ($LASTEXITCODE -ne 0) {
    exit 1
  }
} else {
  & ".venv\Scripts\python.exe" -m research.rl_signal.report --version $rlVersion --model hgb_adv --day $date --start $rlStart --failed "walkforward exit $code"
  exit 1
}
exit 0
