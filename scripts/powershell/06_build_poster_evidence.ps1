<#
.SYNOPSIS
    Build evidence-backed poster tables and figures from generated evidence only.
.DESCRIPTION
    Reads reports/poster/pilot_audit.json, smoke_validation.json (and the derived
    smoke files it points to) and rewrite_verification_summary.json. Refuses to
    draw figures whose evidence is missing or failed validation. Needs no database.
#>
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
try {
    $envValues = $null
    if (Test-Path -LiteralPath (Join-Path $repo '.env')) { $envValues = Import-DotEnv -RepoRoot $repo }
    $python = Get-QcpPython -RepoRoot $repo -EnvValues $envValues
    & $python -m query_cost_predictor build-poster-evidence
    exit $LASTEXITCODE
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name 'BUILD-POSTER-EVIDENCE' -Status 'FAIL'
    exit 1
}
