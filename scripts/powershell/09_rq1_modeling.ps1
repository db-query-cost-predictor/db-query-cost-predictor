<#
.SYNOPSIS
    RQ1 modeling (config/rq1_modeling_v1.yaml). Offline: no Docker and no database.
.DESCRIPTION
    -Step CheckInputs   verify the pinned evidence package (read-only; writes no run directory).
                        Data checks and evidence-run eligibility are reported separately; the
                        original release validation (STOP, G-08) stays visible.
    -Step Run           the single evidence-producing run. It refuses until every stage is
                        implemented and the source is clean and committed; it never skips a stage.
.EXAMPLE
    .\scripts\powershell\09_rq1_modeling.ps1 -Step CheckInputs -EvidenceDir C:\qcp_share\expanded_release -Archive C:\qcp_share\expanded_release.zip
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('CheckInputs', 'Run')]
    [string]$Step,
    [string]$EvidenceDir,
    [string]$Archive,
    [string]$RunLabel,
    [string]$JsonOut
)

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
$statusName = 'RQ1-' + $Step.ToUpper()
try {
    # .env is optional here: the offline input check does not need database settings.
    $envValues = $null
    if (Test-Path -LiteralPath (Join-Path $repo '.env')) { $envValues = Import-DotEnv -RepoRoot $repo }
    $python = Get-QcpPython -RepoRoot $repo -EnvValues $envValues
    $cliArguments = @('-m', 'query_cost_predictor')
    switch ($Step) {
        'CheckInputs' {
            $cliArguments += 'rq1-check-inputs'
            if ($JsonOut) { $cliArguments += @('--json-out', $JsonOut) }
        }
        'Run' {
            if (-not $RunLabel) { throw 'Run needs -RunLabel (a short label recorded in the run record)' }
            $cliArguments += @('rq1-run', '--run-label', $RunLabel)
        }
    }
    if ($EvidenceDir) { $cliArguments += @('--evidence-dir', $EvidenceDir) }
    if ($Archive) { $cliArguments += @('--archive', $Archive) }
    & $python @cliArguments
    $code = $LASTEXITCODE
    if ($Step -eq 'Run' -and $code -eq 3) {
        Write-Host 'REFUSED: no run directory was created and nothing was fitted. Read the message above.'
    }
    exit $code
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name $statusName -Status 'FAIL'
    exit 1
}
