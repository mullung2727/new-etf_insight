$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
New-Item -Force -ItemType Directory $logDir | Out-Null
$log = Join-Path $logDir ("notes-sync-" + (Get-Date -Format "yyyyMMdd") + ".log")

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# 키움 체결 → 투자노트 동기화. date 생략 시 broker가 당일(KST)로 처리.
# broker 미기동 시 연결 실패 로그만 남기고 정상 종료(스케줄러 오류 방지).
try {
  $res = Invoke-RestMethod -Method Post -Uri "http://localhost:8001/notes/sync-trades" -TimeoutSec 60
  "$stamp OK created=$($res.created) updated=$($res.updated) notes=$($res.notes)" |
    Tee-Object -FilePath $log -Append
}
catch {
  "$stamp SKIP $($_.Exception.Message)" | Tee-Object -FilePath $log -Append
}

exit 0
