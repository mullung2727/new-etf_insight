$ErrorActionPreference = "Stop"
$root = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$pidDir = Join-Path $root ".server-pids"
$logDir = Join-Path $root "ops\logs"
$webDir = Join-Path $root "broker-web"
$prodPort = 3100

# 공용 detached 런처 (Stop-*, Start-DetachedServer, Wait-Http)
. (Join-Path $PSScriptRoot "lib_detached_server.ps1")

New-Item -ItemType Directory -Path $pidDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

# 1) prod 빌드 (.next-prod). dev의 .next 와 분리되어 dev(:3000)에 영향 없음.
Write-Host "=== BUILD broker-web (prod → .next-prod) ===" -ForegroundColor Cyan
Push-Location $webDir
try {
    $env:NEXT_DIST_DIR = ".next-prod"
    & npm.cmd run build
    $buildCode = $LASTEXITCODE
} finally {
    Remove-Item Env:NEXT_DIST_DIR -ErrorAction SilentlyContinue
    Pop-Location
}
if ($buildCode -ne 0) {
    Write-Host "빌드 실패(code=$buildCode). prod 재기동 취소. 실행 중인 서버는 그대로 둠." -ForegroundColor Red
    exit 1
}

# 2) 기존 prod(:3100) 정리
Write-Host "`r`n=== RESTART prod (:$prodPort) ===" -ForegroundColor Cyan
Stop-PidFile -Name "broker-web" -Path (Join-Path $pidDir "broker-web.pid")
Stop-PortOwner -Port $prodPort -Label "broker-web"
Start-Sleep -Seconds 2

# 3) 새 빌드로 detached 재기동
$prodCmd = 'set "NEXT_DIST_DIR=.next-prod" & npm.cmd run start -- -p 3100'
Start-DetachedServer -Name "broker-web" -WorkDir $webDir -Title "broker-web :$prodPort (prod)" -CommandLine $prodCmd

# 4) 검증
Write-Host "`r`n=== HEALTH CHECK ===" -ForegroundColor Cyan
$okWeb = Wait-Http -Name "broker-web" -Url "http://localhost:$prodPort"
Start-Sleep -Seconds 3
$listening = Get-NetTCPConnection -LocalPort $prodPort -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }

if ($okWeb -and $listening) {
    Write-Host "`r`nDone. prod 반영 완료 → http://localhost:$prodPort  (.next-prod)" -ForegroundColor Green
    Write-Host "dev는 그대로 http://localhost:3000 (영향 없음)." -ForegroundColor DarkGray
    exit 0
} else {
    Write-Host "`r`nprod 기동 실패. 로그: $logDir\broker-web-*.err.log" -ForegroundColor Red
    exit 1
}
