$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

# 리서치 호가 녹화기: 08:20 시작, 스크립트가 08:30까지 대기 후 20:00에 스스로 종료.
# 휴장일 판정은 스크립트가 수행 (러너에 휴장 가드 없음).
# 종료 코드: 0 정상(창 종료·휴장), 3 안전 중단(스크립트가 이미 알림 보냄), 1 오류. 그대로 exit.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" "scripts\run_orderbook_research_recorder.py" `
  *> (Join-Path $logDir "orderbook-research-recorder-$date.log")
$code = $LASTEXITCODE
if ($null -eq $code) { exit 1 }
$ErrorActionPreference = "Stop"

exit $code
