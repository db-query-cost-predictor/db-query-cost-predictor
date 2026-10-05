<#
.SYNOPSIS
    Stop bench-db and evidence-db. Volumes (benchmark data and evidence) are kept.
#>
[CmdletBinding()]
param()

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
try {
    $null = Import-DotEnv -RepoRoot $repo
    Invoke-Checked -FilePath 'docker' -Arguments @('compose', 'stop', 'bench-db', 'evidence-db') -What 'stop bench-db and evidence-db (volumes are kept)'
    Write-FinalStatus -Name 'STOP-DATABASES' -Status 'PASS'
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name 'STOP-DATABASES' -Status 'FAIL'
    exit 1
}
