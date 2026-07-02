param(
    [string]$RegistryPath = "$PSScriptRoot\openclaw-cron.registry.json",
    [string]$JobName,
    [switch]$CommandsOnly
)

$ErrorActionPreference = "Stop"

& "$PSScriptRoot\Test-OpenClawBatchRegistry.ps1" -RegistryPath $RegistryPath -Quiet | Out-Null

$registryFile = Resolve-Path -LiteralPath $RegistryPath
$projectRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")
$registry = Get-Content -LiteralPath $registryFile -Raw -Encoding UTF8 | ConvertFrom-Json

$jobs = @($registry.jobs | Where-Object { $_.windowsTask -and $_.windowsTask.enabled })
if ($JobName) {
    $jobs = @($jobs | Where-Object { $_.name -eq $JobName })
    if ($jobs.Count -eq 0) {
        throw "Unknown Windows task job name: $JobName"
    }
}

$specs = foreach ($job in $jobs) {
    $task = $job.windowsTask
    $xmlPath = Join-Path $projectRoot $task.xmlFile
    $command = 'Register-ScheduledTask -Xml (Get-Content -Path "{0}" -Raw -Encoding UTF8) -TaskName "{1}" -TaskPath "{2}" -Force' -f $xmlPath, $task.taskName, $task.taskPath
    if ($CommandsOnly) {
        $command
    } else {
        [ordered]@{
            jobName = $job.name
            taskName = $task.taskName
            taskPath = $task.taskPath
            schedule = $job.schedule
            xmlPath = $xmlPath
            runnerScript = Join-Path $projectRoot $task.runnerScript
            delivery = $task.delivery
            registerCommand = $command
        }
    }
}

if ($CommandsOnly) {
    $specs | Write-Output
} else {
    ConvertTo-Json -InputObject @($specs) -Depth 20
}
