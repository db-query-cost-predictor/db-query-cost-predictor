<#
.SYNOPSIS
    Run the independent, read-only pilot audit and check that its outputs exist.
.DESCRIPTION
    Does not need the databases. Reads reference_pilot/output read-only and writes
    reports/poster/pilot_audit.json, pilot_metrics.csv, pilot_claim_check.md,
    pilot_data_quality.csv and figures under reports/poster/figures/pilot/.
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
    & $python -m query_cost_predictor pilot-audit
    if ($LASTEXITCODE -ne 0) { throw "pilot-audit exited with code $LASTEXITCODE" }

    $expected = @('reports\poster\pilot_audit.json', 'reports\poster\pilot_metrics.csv',
                  'reports\poster\pilot_claim_check.md', 'reports\poster\pilot_data_quality.csv')
    $missing = @($expected | Where-Object { -not (Test-Path -LiteralPath (Join-Path $repo $_)) })
    $figures = @(Get-ChildItem -LiteralPath (Join-Path $repo 'reports\poster\figures\pilot') -Filter '*.png' -ErrorAction SilentlyContinue)
    Write-Host ("pilot figures written: {0}" -f $figures.Count)
    if ($missing.Count -gt 0) { throw ("missing outputs: " + ($missing -join ', ')) }
    $quality = @(Import-Csv -LiteralPath (Join-Path $repo 'reports\poster\pilot_data_quality.csv'))
    $failRows = @($quality | Where-Object { $_.status -eq 'FAIL' })
    Write-Host ("data-quality rows: {0} total, {1} FAIL" -f $quality.Count, $failRows.Count)
    if ($failRows.Count -gt 0) {
        $failRows | Format-Table check_id, check, observed, expected -AutoSize | Out-String | Write-Host
        Write-FinalStatus -Name 'PILOT-AUDIT' -Status 'FAIL (data-quality FAIL rows; inspect before using any pilot number)'
        exit 1
    }
    Write-FinalStatus -Name 'PILOT-AUDIT' -Status 'COMPLETED'
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name 'PILOT-AUDIT' -Status 'FAIL'
    exit 1
}
