$ErrorActionPreference = "Stop"
$root = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$pidDir = Join-Path $root ".server-pids"
$logDir = Join-Path $root "ops\logs"

# 공용 detached 런처 (Add-Type NativeProc, Stop-*, Start-DetachedServer, Wait-Http)
. (Join-Path $PSScriptRoot "lib_detached_server.ps1")

Write-Host "=== KILLING existing servers ===" -ForegroundColor Yellow

# broker-web 상시 = prod 빌드(.next-prod)를 :3100 에서 next start. dev(:3000)은 개발 시 수동.
$ports = @(8000, 8001, 3100)
$names = @{ 8000 = "api"; 8001 = "broker"; 3100 = "broker-web" }

if (Test-Path $pidDir) {
    Stop-PidFile -Name "api" -Path (Join-Path $pidDir "api.pid")
    Stop-PidFile -Name "broker" -Path (Join-Path $pidDir "broker.pid")
    Stop-PidFile -Name "broker-web" -Path (Join-Path $pidDir "broker-web.pid")
}

foreach ($port in $ports) {
    Stop-PortOwner -Port $port -Label $names[$port]
}

Start-Sleep -Seconds 2
New-Item -ItemType Directory -Path $pidDir -Force | Out-Null
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Write-Host "`r`n=== STARTING servers (detached / always-on) ===" -ForegroundColor Green

# --reload 제거: 워크스페이스 파일 감시 reloader가 에이전트 수정/저장에 민감하고, 부모-자식 트리가 불안정.
Start-DetachedServer -Name "api" -WorkDir (Join-Path $root "api") -Title "api :8000" `
    -CommandLine ".\.venv\Scripts\python.exe -m uvicorn main:app --port 8000 --host 127.0.0.1"

Start-DetachedServer -Name "broker" -WorkDir (Join-Path $root "broker") -Title "broker :8001" `
    -CommandLine ".\.venv\Scripts\python.exe -m uvicorn main:app --port 8001 --host 127.0.0.1"

# prod 빌드가 없으면 next start가 실패한다 → 최초/변경 후엔 scripts\deploy_broker_web_prod.ps1 먼저.
$prodBuild = Join-Path $root "broker-web\.next-prod\BUILD_ID"
if (-not (Test-Path $prodBuild)) {
    Write-Host "  [warn] broker-web prod 빌드(.next-prod) 없음 → :3100 기동 실패 예상." -ForegroundColor Yellow
    Write-Host "         먼저 실행: .\scripts\deploy_broker_web_prod.ps1" -ForegroundColor Yellow
}
$webProdCmd = 'set "NEXT_DIST_DIR=.next-prod" & npm.cmd run start -- -p 3100'
Start-DetachedServer -Name "broker-web" -WorkDir (Join-Path $root "broker-web") -Title "broker-web :3100 (prod)" -CommandLine $webProdCmd

Write-Host "`r`n=== HEALTH CHECK ===" -ForegroundColor Cyan
$okApi = Wait-Http -Name "api" -Url "http://localhost:8000/health"
$okBroker = Wait-Http -Name "broker" -Url "http://localhost:8001/health"
$okWeb = Wait-Http -Name "broker-web" -Url "http://localhost:3100"

# 스크립트 종료 직후에도 잡 킬에 안 당했는지 한 번 더 확인
Start-Sleep -Seconds 3
Write-Host "`r`n=== DURABILITY CHECK (3s later) ===" -ForegroundColor Cyan
$still = @()
foreach ($port in $ports) {
    $listening = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq "Listen" }
    if ($listening) {
        Write-Host "  :$port still listening" -ForegroundColor Green
        $still += $port
    } else {
        Write-Host "  :$port DIED after start — not durable" -ForegroundColor Red
    }
}

# 추가: PID 파일 프로세스가 살아 있는지
Write-Host "`r`n=== PID LIVENESS ===" -ForegroundColor Cyan
foreach ($n in @("api", "broker", "broker-web")) {
    $pf = Join-Path $pidDir "$n.pid"
    if (Test-Path $pf) {
        $id = (Get-Content $pf -Raw).Trim()
        $alive = $null -ne (Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue)
        if ($alive) {
            Write-Host "  $n pid=$id alive" -ForegroundColor Green
        } else {
            Write-Host "  $n pid=$id DEAD" -ForegroundColor Red
        }
    }
}

Write-Host "`r`nDone. Servers started detached (no window, job-breakaway). PIDs: $pidDir" -ForegroundColor Green
Write-Host "Logs: $logDir\<name>-yyyyMMdd.log (console + file)" -ForegroundColor Cyan
Write-Host "Health: http://localhost:8000/health  http://localhost:8001/health  http://localhost:3100" -ForegroundColor Cyan
Write-Host "Note: broker-web=prod(:3100, .next-prod). 코드 반영은 .\scripts\deploy_broker_web_prod.ps1. dev는 별도 npm run dev(:3000)." -ForegroundColor DarkGray

if ($still.Count -lt 3 -or -not ($okApi -and $okBroker -and $okWeb)) {
    exit 1
}
exit 0
