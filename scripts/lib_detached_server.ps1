# 공용 상시 서버 런처. restart_all_servers.ps1 / deploy_broker_web_prod.ps1 이 dot-source.
# 호출 전에 $pidDir, $logDir, $root 를 스크립트 스코프에 정의해 둘 것.

# Win32 CreateProcess — Job Object / 콘솔 창에 묶이지 않는 상시 기동 (중복 dot-source 대비 가드)
if (-not ('NativeProc' -as [type])) {
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
}

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

function Stop-PortOwner {
    param([int]$Port, [string]$Label)

    $conns = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" }
    if ($conns) {
        $processIds = $conns.OwningProcess | Select-Object -Unique
        foreach ($processId in $processIds) {
            $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "  killing $Label port owner (PID $processId, $($proc.ProcessName))" -ForegroundColor Red
                Stop-ProcessTree -ProcessId ([int]$processId)
            }
        }
    } else {
        Write-Host "  $Label :$Port not running" -ForegroundColor DarkGray
    }
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
