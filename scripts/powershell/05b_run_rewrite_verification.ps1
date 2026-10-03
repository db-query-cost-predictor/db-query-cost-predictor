<#
.SYNOPSIS
    Run the manual reference-rewrite verification harness (no LLM is called).
.DESCRIPTION
    Writes raw evidence under QCP_DATA_ROOT\raw\rewrite_verification and the
    summary reports/poster/rewrite_verification_summary.json. Every result is
    labelled MANUAL_REFERENCE_REWRITE.
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
    & $python -m query_cost_predictor verify-rewrites
    exit $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name 'REWRITE-VERIFY' -Status 'FAIL'
    exit 1
}
