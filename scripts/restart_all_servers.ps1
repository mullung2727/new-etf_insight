$ErrorActionPreference = "Stop"
$root = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$pidDir = Join-Path $root ".server-pids"

Write-Host "=== KILLING existing servers ===" -ForegroundColor Yellow

$ports = @(8000, 8001, 3000)
$names = @{8000="api"; 8001="broker"; 3000="broker-web"}

function Stop-ProcessTree {
    param([int]$ProcessId)

    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }

    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-PidFile {
    param(
        [string]$Name,
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return
    }

    $storedPid = Get-Content $Path -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($storedPid -match '^\d+$') {
        $procInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $storedPid" -ErrorAction SilentlyContinue
        $proc = Get-Process -Id ([int]$storedPid) -ErrorAction SilentlyContinue
        if ($proc -and $procInfo -and (($procInfo.CommandLine -as [string]) -like "*$root*")) {
            Write-Host "  killing previous $Name launcher (PID $storedPid)" -ForegroundColor Red
            Stop-ProcessTree -ProcessId ([int]$storedPid)
        } elseif ($proc) {
            Write-Host "  stale $Name PID file ignored (PID $storedPid is not this project)" -ForegroundColor Yellow
        }
    }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

if (Test-Path $pidDir) {
    Stop-PidFile -Name "api" -Path (Join-Path $pidDir "api.pid")
    Stop-PidFile -Name "broker" -Path (Join-Path $pidDir "broker.pid")
    Stop-PidFile -Name "broker-web" -Path (Join-Path $pidDir "broker-web.pid")
}

foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($conns) {
        $processIds = $conns.OwningProcess | Select-Object -Unique
        foreach ($processId in $processIds) {
            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "  killing $($names[$port]) port owner (PID $processId, $($proc.ProcessName))" -ForegroundColor Red
                Stop-ProcessTree -ProcessId ([int]$processId)
            }
        }
    } else {
        Write-Host "  $($names[$port]) :$port not running" -ForegroundColor DarkGray
    }
}

Start-Sleep -Seconds 2
New-Item -ItemType Directory -Path $pidDir -Force | Out-Null

Write-Host "`r`n=== STARTING servers ===" -ForegroundColor Green

# 1) api (:8000)
$api = Start-Process powershell -PassThru -ArgumentList @(
    "-NoExit",
    "-Command",
    "`$host.UI.RawUI.WindowTitle='api :8000'; cd '$root\api'; .\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000"
)
$api.Id | Set-Content -Encoding ascii (Join-Path $pidDir "api.pid")

# 2) broker (:8001)
$broker = Start-Process powershell -PassThru -ArgumentList @(
    "-NoExit",
    "-Command",
    "`$host.UI.RawUI.WindowTitle='broker :8001'; cd '$root\broker'; .\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001"
)
$broker.Id | Set-Content -Encoding ascii (Join-Path $pidDir "broker.pid")

# 3) broker-web (:3000)
$web = Start-Process powershell -PassThru -ArgumentList @(
    "-NoExit",
    "-Command",
    "`$host.UI.RawUI.WindowTitle='broker-web :3000'; cd '$root\broker-web'; npm run dev"
)
$web.Id | Set-Content -Encoding ascii (Join-Path $pidDir "broker-web.pid")

function Wait-Http {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $res = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($res.StatusCode -ge 200 -and $res.StatusCode -lt 500) {
                Write-Host "  $Name ready: $Url ($($res.StatusCode))" -ForegroundColor Green
                return
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    Write-Host "  $Name not ready after ${TimeoutSeconds}s: $Url" -ForegroundColor Yellow
}

Write-Host "`r`n=== HEALTH CHECK ===" -ForegroundColor Cyan
Wait-Http -Name "api" -Url "http://localhost:8000/health"
Wait-Http -Name "broker" -Url "http://localhost:8001/health"
Wait-Http -Name "broker-web" -Url "http://localhost:3000"

Write-Host "`r`nDone. 3 windows opened: api, broker, broker-web" -ForegroundColor Green
Write-Host "Health check: curl http://localhost:8000/health   (api)" -ForegroundColor Cyan
Write-Host "             curl http://localhost:8001/health   (broker)" -ForegroundColor Cyan
Write-Host "             http://localhost:3000               (broker-web)" -ForegroundColor Cyan

