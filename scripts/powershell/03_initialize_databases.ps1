<#
.SYNOPSIS
    Create roles and databases and apply the evidence migrations (idempotent).
.DESCRIPTION
    Requires both containers to be healthy (step 5). Runs
    `python -m query_cost_predictor db-init`, which uses the container
    administrator accounts only for initialization and writes
    reports/poster/environment/db_init.json.
#>
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
try {
    $envValues = Import-DotEnv -RepoRoot $repo
    $python = Get-QcpPython -RepoRoot $repo -EnvValues $envValues
    foreach ($service in @('bench-db', 'evidence-db')) {
        if (-not (Test-ComposeServiceHealthy -Service $service)) { throw "$service is not running or not healthy; run 02_start_databases.ps1 first" }
    }
    & $python -m query_cost_predictor db-init
    exit $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name 'DB-INIT' -Status 'FAIL'
    exit 1
}
