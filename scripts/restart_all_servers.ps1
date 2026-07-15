$ErrorActionPreference = "Stop"
$root = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight"
$pidDir = Join-Path $root ".server-pids"
$logDir = Join-Path $root "ops\logs"

Write-Host "=== KILLING existing servers ===" -ForegroundColor Yellow

$ports = @(8000, 8001, 3000)
$names = @{ 8000 = "api"; 8001 = "broker"; 3000 = "broker-web" }

# Win32 CreateProcess — Job Object / 콘솔 창에 묶이지 않는 상시 기동
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class NativeProc {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct STARTUPINFO {
    public int cb;
    public IntPtr lpReserved;
    public IntPtr lpDesktop;
    public IntPtr lpTitle;
    public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
    public short wShowWindow, cbReserved2;
    public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
  }
  [StructLayout(LayoutKind.Sequential)]
  public struct PROCESS_INFORMATION {
    public IntPtr hProcess, hThread;
    public int dwProcessId, dwThreadId;
  }
  [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern bool CreateProcess(
    string lpApplicationName,
    StringBuilder lpCommandLine,
    IntPtr lpProcessAttributes,
    IntPtr lpThreadAttributes,
    bool bInheritHandles,
    uint dwCreationFlags,
    IntPtr lpEnvironment,
    string lpCurrentDirectory,
    ref STARTUPINFO lpStartupInfo,
    out PROCESS_INFORMATION lpProcessInformation);
  [DllImport("kernel32.dll", SetLastError = true)]
  public static extern bool CloseHandle(IntPtr hObject);
  public const uint CREATE_BREAKAWAY_FROM_JOB = 0x01000000;
  public const uint CREATE_NEW_PROCESS_GROUP = 0x00000200;
  public const uint CREATE_NO_WINDOW = 0x08000000;
  public const uint DETACHED_PROCESS = 0x00000008;
}
"@ -ErrorAction Stop

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

function Start-DetachedServer {
    <#
    상시 서버 기동 (에이전트 Job 종료·콘솔 닫기와 무관하게 생존):
    - CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    - 최소 PowerShell 창을 열지 않음 (이전 방식: Minimized 콘솔 → 닫히면 전멸)
    - cmd 래퍼로 stdout/stderr 파일 리다이렉트
    - 상시 모드: uvicorn --reload 제거 (워크스페이스 파일 변경 시 reloader 불안정)
    #>
    param(
        [string]$Name,
        [string]$WorkDir,
        [string]$CommandLine,
        [string]$Title
    )

    $pidFile = Join-Path $pidDir "$Name.pid"
    $outLog = Join-Path $logDir ("{0}-{1}.out.log" -f $Name, (Get-Date -Format "yyyyMMdd"))
    $errLog = Join-Path $logDir ("{0}-{1}.err.log" -f $Name, (Get-Date -Format "yyyyMMdd"))
    $cmdFile = Join-Path $pidDir "$Name-run.cmd"

    # cmd 래퍼: 작업 디렉터리 + 로그 append. 창 없음.
    $cmdBody = @"
@echo off
setlocal
cd /d "$WorkDir"
echo.>> "$outLog"
echo ===== %DATE% %TIME% start $Title =====>> "$outLog"
$CommandLine >> "$outLog" 2>> "$errLog"
echo ===== %DATE% %TIME% exit $Title code=%ERRORLEVEL% =====>> "$outLog"
"@
    Set-Content -LiteralPath $cmdFile -Value $cmdBody -Encoding ASCII

    $si = New-Object NativeProc+STARTUPINFO
    $si.cb = [Runtime.InteropServices.Marshal]::SizeOf($si)
    $pi = New-Object NativeProc+PROCESS_INFORMATION

    # CreateProcessW mutates command line → StringBuilder 필수. cmd 전체 경로 사용.
    $cmdExe = Join-Path $env:SystemRoot "System32\cmd.exe"
    $cmdLine = New-Object System.Text.StringBuilder 1024
    [void]$cmdLine.Append('"' + $cmdExe + '" /c "' + $cmdFile + '"')
    $flags = [NativeProc]::CREATE_BREAKAWAY_FROM_JOB -bor `
             [NativeProc]::CREATE_NEW_PROCESS_GROUP -bor `
             [NativeProc]::CREATE_NO_WINDOW

    $ok = [NativeProc]::CreateProcess(
        $cmdExe,
        $cmdLine,
        [IntPtr]::Zero,
        [IntPtr]::Zero,
        $false,
        $flags,
        [IntPtr]::Zero,
        $WorkDir,
        [ref]$si,
        [ref]$pi
    )
    $processId = 0
    $mode = "breakaway"

    if ($ok) {
        $processId = $pi.dwProcessId
        [void][NativeProc]::CloseHandle($pi.hThread)
        [void][NativeProc]::CloseHandle($pi.hProcess)
    } else {
        $err = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        # BREAKAWAY 불가(에이전트 Job 등): CreateProcess no-breakaway 는 Job에 묶여
        # 세션 종료 시 같이 죽는다 → WMI Win32_Process.Create 로 Job 밖 기동.
        if ($err -eq 5 -or $err -eq 24 -or $err -eq 3 -or $err -eq 208) {
            $mode = "wmi"
            $wmiCmd = '"' + $cmdExe + '" /c "' + $cmdFile + '"'
            $wmi = $null
            try {
                $wmi = ([wmiclass]"Win32_Process").Create($wmiCmd, $WorkDir, $null)
            } catch {
                throw "Failed to start detached server via WMI: ${Name}: $($_.Exception.Message) (CreateProcess Win32=$err)"
            }
            if (-not $wmi -or [int]$wmi.ReturnValue -ne 0) {
                $rv = if ($wmi) { $wmi.ReturnValue } else { "null" }
                throw "Failed to start detached server via WMI: ${Name} ReturnValue=$rv (CreateProcess Win32=$err)"
            }
            $processId = [int]$wmi.ProcessId
            Write-Host "  ${Name}: breakaway unavailable (Win32=$err), started via WMI (job-escape)" -ForegroundColor Yellow
        } else {
            throw "Failed to start detached server: ${Name} (Win32=$err)"
        }
    }

    if ($processId -le 0) {
        throw "Failed to start detached server: ${Name} (no process id)"
    }
    Set-Content -LiteralPath $pidFile -Value $processId -Encoding ascii
    Write-Host "  started ${Name} (pid=$processId, mode=$mode, title='$Title')" -ForegroundColor Green
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
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

Write-Host "`r`n=== STARTING servers (detached / always-on) ===" -ForegroundColor Green

# --reload 제거: 워크스페이스 파일 감시 reloader가 에이전트 수정/저장에 민감하고, 부모-자식 트리가 불안정.
Start-DetachedServer -Name "api" -WorkDir (Join-Path $root "api") -Title "api :8000" `
    -CommandLine ".\.venv\Scripts\python.exe -m uvicorn main:app --port 8000 --host 127.0.0.1"

Start-DetachedServer -Name "broker" -WorkDir (Join-Path $root "broker") -Title "broker :8001" `
    -CommandLine ".\.venv\Scripts\python.exe -m uvicorn main:app --port 8001 --host 127.0.0.1"

Start-DetachedServer -Name "broker-web" -WorkDir (Join-Path $root "broker-web") -Title "broker-web :3000" `
    -CommandLine "npm.cmd run dev"

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
                return $true
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    Write-Host "  $Name not ready after ${TimeoutSeconds}s: $Url" -ForegroundColor Yellow
    return $false
}

Write-Host "`r`n=== HEALTH CHECK ===" -ForegroundColor Cyan
$okApi = Wait-Http -Name "api" -Url "http://localhost:8000/health"
$okBroker = Wait-Http -Name "broker" -Url "http://localhost:8001/health"
$okWeb = Wait-Http -Name "broker-web" -Url "http://localhost:3000"

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
Write-Host "Logs: $logDir\<name>-yyyyMMdd.(out|err).log" -ForegroundColor Cyan
Write-Host "Health: http://localhost:8000/health  http://localhost:8001/health  http://localhost:3000" -ForegroundColor Cyan
Write-Host "Note: uvicorn --reload OFF for stability. Code change → re-run this script." -ForegroundColor DarkGray

if ($still.Count -lt 3 -or -not ($okApi -and $okBroker -and $okWeb)) {
    exit 1
}
exit 0
