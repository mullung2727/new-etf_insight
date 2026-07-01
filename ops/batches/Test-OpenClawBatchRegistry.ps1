param(
    [string]$RegistryPath = "$PSScriptRoot\openclaw-cron.registry.json"
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
}

Write-Host "OK: $($registry.jobs.Count) OpenClaw batch jobs validated from $registryFile"
