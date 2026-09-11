# 호가 스냅샷 수집기 런처 — 평일 08:44(오전 구간), 14:59(오후 구간). 같은 러너를 두 번 부른다.
# 구간은 스크립트가 현재 시각으로 고른다. 설정: etl/scripts/orderbook_recorder.json (enabled=false 면 즉시 종료).
# 명세: docs/SPEC_ORDERBOOK_SNAPSHOT_RECORDER.md §11
$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("orderbook-recorder-" + (Get-Date -Format "yyyyMMdd") + ".log")

Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

# 오전·오후가 같은 날짜 로그를 쓰므로 -Append. stderr(경고·traceback)를 로그에 합칠 때
# EAP=Stop 이면 PS 5.1 이 첫 stderr 줄에서 러너를 죽이므로 호출 구간만 Continue.
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" "scripts\run_orderbook_recorder.py" 2>&1 | Tee-Object -FilePath $log -Append
exit $LASTEXITCODE
