$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

# 호가 압축 보관: 완료된 당일 sqlite(C:)를 parquet(D:)으로 변환·검증 후 C: 복사본 삭제.
# 종료 코드 0이 아니면 exit 1.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" "scripts\orderbook_archive.py" `
  *> (Join-Path $logDir "orderbook-archive-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($code -ne 0) { exit 1 }
exit 0
