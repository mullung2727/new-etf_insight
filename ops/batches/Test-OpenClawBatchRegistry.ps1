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

if (-not $Quiet) {
    Write-Host "OK: $($registry.jobs.Count) project batch jobs validated from $registryFile"
}
