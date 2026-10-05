<#
.SYNOPSIS
    Generate and load TPC-H data for one scale factor, then register the snapshot.
.DESCRIPTION
    Builds the tpch-tools image (pinned dbgen commit), runs the loader inside
    Docker as qcp_bench_owner, and registers an immutable benchmark_snapshot
    (schema, index, statistics and data hashes) in the evidence store.
    The loader refuses to overwrite existing TPC-H tables.
.EXAMPLE
    .\scripts\powershell\03b_load_tpch.ps1 -ScaleFactor 0.1
#>
[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('0.1', '1')][string]$ScaleFactor)

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
try {
    $envValues = Import-DotEnv -RepoRoot $repo
    $python = Get-QcpPython -RepoRoot $repo -EnvValues $envValues
    foreach ($service in @('bench-db', 'evidence-db')) {
        if (-not (Test-ComposeServiceHealthy -Service $service)) { throw "$service is not running or not healthy; run 02_start_databases.ps1 first" }
    }
    Invoke-Checked -FilePath 'docker' -Arguments @('compose', '--profile', 'tools', 'build', 'tpch-tools') -What 'build the tpch-tools image (first build clones the pinned dbgen commit)'
    Invoke-Checked -FilePath 'docker' -Arguments @('compose', '--profile', 'tools', 'run', '--rm', 'tpch-tools', $ScaleFactor) -What "generate and load TPC-H SF $ScaleFactor"
    Invoke-Checked -FilePath $python -Arguments @('-m', 'query_cost_predictor', 'register-snapshot', '--scale-factor', $ScaleFactor) -What 'register the benchmark snapshot'
    Write-FinalStatus -Name 'LOAD-TPCH' -Status 'PASS'
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name 'LOAD-TPCH' -Status 'FAIL'
    exit 1
}
