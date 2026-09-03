param(
    [string]$RegistryPath = "$PSScriptRoot\openclaw-cron.registry.json",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$registryFile = Resolve-Path -LiteralPath $RegistryPath
$projectRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")
$registry = Get-Content -LiteralPath $registryFile -Raw -Encoding UTF8 | ConvertFrom-Json

if ($registry.schemaVersion -ne 1) {
    throw "Unsupported schemaVersion: $($registry.schemaVersion)"
}

if (-not $registry.jobs -or $registry.jobs.Count -eq 0) {
    throw "Registry has no jobs"
}

$names = @{}
$ids = @{}

foreach ($job in $registry.jobs) {
    foreach ($field in @("id", "name", "description", "enabled", "schedule", "instructionFile", "timeoutSeconds")) {
        # 존재 여부만 검사. ($job.$field -eq "") 는 enabled:$false 를 "" 로 오판정하므로 쓰지 않음.
        if ($null -eq $job.$field -or ($job.$field -is [string] -and $job.$field -eq "")) {
            throw "Job is missing required field '$field': $($job | ConvertTo-Json -Compress)"
        }
    }

    if ($names.ContainsKey($job.name)) {
        throw "Duplicate job name: $($job.name)"
    }
    $names[$job.name] = $true

    if ($ids.ContainsKey($job.id)) {
        throw "Duplicate job id: $($job.id)"
    }
    $ids[$job.id] = $true

    if ($job.schedule.kind -ne "cron") {
        throw "Only cron schedules are supported here: $($job.name)"
    }
    if (-not $job.schedule.expr -or -not $job.schedule.tz) {
        throw "Cron schedule needs expr and tz: $($job.name)"
    }
    if ($job.timeoutSeconds -lt 60) {
        throw "timeoutSeconds looks too small for $($job.name): $($job.timeoutSeconds)"
    }

    $instructionPath = Join-Path $projectRoot $job.instructionFile
    if (-not (Test-Path -LiteralPath $instructionPath -PathType Leaf)) {
        throw "Instruction file is missing for $($job.name): $instructionPath"
    }

    if ($job.windowsTask) {
        foreach ($field in @("enabled", "taskPath", "taskName", "xmlFile", "runnerScript", "delivery")) {
            if ($null -eq $job.windowsTask.$field -or ($job.windowsTask.$field -is [string] -and $job.windowsTask.$field -eq "")) {
                throw "windowsTask is missing required field '$field' for $($job.name)"
            }
        }
        if ($job.windowsTask.delivery.kind -ne "discordWebhook") {
            throw "Unsupported windowsTask delivery kind for $($job.name): $($job.windowsTask.delivery.kind)"
        }
        if (-not $job.windowsTask.delivery.env) {
            throw "windowsTask Discord delivery needs env for $($job.name)"
        }

        $xmlPath = Join-Path $projectRoot $job.windowsTask.xmlFile
        if (-not (Test-Path -LiteralPath $xmlPath -PathType Leaf)) {
            throw "Windows task XML is missing for $($job.name): $xmlPath"
        }
        [xml]$xml = Get-Content -LiteralPath $xmlPath -Raw -Encoding UTF8
        if (-not $xml.Task.Actions.Exec.Command) {
            throw "Windows task XML has no Exec command for $($job.name): $xmlPath"
        }

        $runnerPath = Join-Path $projectRoot $job.windowsTask.runnerScript
        if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
            throw "Windows task runner is missing for $($job.name): $runnerPath"
        }
    }
}

# 파일 존재만 검사하면 "레지스트리엔 있는데 실제로는 등록 안 된" 배치를 못 잡는다.
# 2026-09-04: daily-naver-research 가 정확히 그 상태로 2개월 방치됐다.
# throw 하지 않고 경고만 낸다. Export-WindowsScheduledTaskSpecs.ps1 이 등록 명령을 뽑기 전에
# 이 스크립트를 먼저 통과시키므로, throw 하면 미등록 작업을 영영 등록할 수 없다.
$declared = @($registry.jobs | Where-Object { $_.windowsTask -and $_.windowsTask.enabled })
foreach ($taskPath in @($declared.windowsTask.taskPath | Sort-Object -Unique)) {
    $registered = @((Get-ScheduledTask -TaskPath $taskPath -ErrorAction SilentlyContinue).TaskName)
    $expected = @(($declared | Where-Object { $_.windowsTask.taskPath -eq $taskPath }).windowsTask.taskName)

    foreach ($missing in @($expected | Where-Object { $_ -notin $registered })) {
        Write-Warning "Declared in registry but not registered in Windows: $taskPath$missing"
    }
    foreach ($extra in @($registered | Where-Object { $_ -notin $expected })) {
        Write-Warning "Registered in Windows but missing from registry: $taskPath$extra"
    }
}

if (-not $Quiet) {
    Write-Host "OK: $($registry.jobs.Count) project batch jobs validated from $registryFile"
}
