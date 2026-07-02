param(
    [string]$RegistryPath = "$PSScriptRoot\openclaw-cron.registry.json",
    [string]$JobName,
    [switch]$Compress
)

$ErrorActionPreference = "Stop"

# registry 유효성 먼저 검증 (schema/필수필드/중복/instruction 파일 존재)
& "$PSScriptRoot\Test-OpenClawBatchRegistry.ps1" -RegistryPath $RegistryPath -Quiet | Out-Null

$registryFile = Resolve-Path -LiteralPath $RegistryPath
$projectRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")
$registry = Get-Content -LiteralPath $registryFile -Raw -Encoding UTF8 | ConvertFrom-Json

$jobs = @($registry.jobs)
if ($JobName) {
    $jobs = @($jobs | Where-Object { $_.name -eq $JobName })
    if ($jobs.Count -eq 0) {
        throw "Unknown job name: $JobName"
    }
}

$specs = foreach ($job in $jobs) {
    $instructionPath = Join-Path $projectRoot $job.instructionFile
    $messageLines = @(
        "Run the project-owned OpenClaw batch instruction file:",
        "",
        $instructionPath,
        "",
        "Treat ops/batches/openclaw-cron.registry.json as the source of truth for schedule, delivery, timeout, and OpenClaw job parameters.",
        "Treat the instruction file as the source of truth for execution steps, verification, and reporting.",
        "If older cron text or memory conflicts with the project files, follow the project files.",
        "",
        $registry.defaults.fileReadingRule
    )

    if ($job.runNote) {
        $messageLines += @("", $job.runNote)
    }

    $messageLines += @("", "After completion, report the result to Discord according to the project file.")

    [ordered]@{
        jobId = $job.id
        name = $job.name
        description = $job.description
        enabled = [bool]$job.enabled
        agentId = $registry.defaults.agentId
        schedule = $job.schedule
        sessionTarget = $registry.defaults.sessionTarget
        wakeMode = $registry.defaults.wakeMode
        payload = [ordered]@{
            kind = $registry.defaults.payloadKind
            message = ($messageLines -join "`n")
            timeoutSeconds = [int]$job.timeoutSeconds
        }
        delivery = if ($job.delivery) { $job.delivery } else { $registry.defaults.delivery }
    }
}

$depth = 20
# -InputObject 로 전달해야 단건도 배열 유지 ($specs | ConvertTo-Json 은 파이프에서 단일원소를 언랩)
if ($Compress) {
    ConvertTo-Json -InputObject @($specs) -Depth $depth -Compress
} else {
    ConvertTo-Json -InputObject @($specs) -Depth $depth
}
