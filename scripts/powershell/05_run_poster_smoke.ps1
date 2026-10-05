<#
.SYNOPSIS
    Poster-smoke collection in four separately inspectable steps.
.DESCRIPTION
    -Step Manifest  build and register the manifest (no query is executed)
    -Step Collect   run or resume the collection (safe to re-run after interruption)
    -Step Derive    rebuild labels and features from raw evidence
    -Step Validate  write reports/poster/smoke_validation.json and .md
.EXAMPLE
    .\scripts\powershell\05_run_poster_smoke.ps1 -Step Manifest
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Manifest', 'Collect', 'Derive', 'Validate')][string]$Step,
    [string]$ManifestId
)

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
$commands = @{ Manifest = 'smoke-manifest'; Collect = 'smoke-collect'; Derive = 'smoke-derive'; Validate = 'smoke-validate' }
$statusNames = @{ Manifest = 'SMOKE-MANIFEST'; Collect = 'SMOKE-COLLECT'; Derive = 'SMOKE-DERIVE'; Validate = 'SMOKE-VALIDATE' }
try {
    $envValues = Import-DotEnv -RepoRoot $repo
    $python = Get-QcpPython -RepoRoot $repo -EnvValues $envValues
    $needed = @('evidence-db')
    if ($Step -in @('Manifest', 'Collect')) { $needed += 'bench-db' }
    foreach ($service in $needed) {
        if (-not (Test-ComposeServiceHealthy -Service $service)) { throw "$service is not running or not healthy; run 02_start_databases.ps1 first" }
    }
    $cliArguments = @('-m', 'query_cost_predictor', $commands[$Step])
    if ($ManifestId -and $Step -ne 'Manifest') { $cliArguments += @('--manifest-id', $ManifestId) }
    & $python @cliArguments
    $code = $LASTEXITCODE
    if ($Step -eq 'Collect' -and $code -eq 130) {
        Write-Host 'Collection interrupted. Re-run the same command to resume; completed modeling_key + repeat_no pairs are skipped.'
    }
    exit $code
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name $statusNames[$Step] -Status 'FAIL'
    exit 1
}
