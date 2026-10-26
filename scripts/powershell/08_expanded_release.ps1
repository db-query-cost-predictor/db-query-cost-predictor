<#
.SYNOPSIS
    Expanded release (config/expanded_release_v1.yaml) in separately inspectable steps.
.DESCRIPTION
    -Step Arithmetic      proposed-key arithmetic from the spec (no database, nothing executed)
    -Step Readiness       fresh preflight, migration V008, both snapshots, free disk
    -Step Manifest        build and register the manifest (no query is executed)
    -Step PlanCheck       plain EXPLAIN of every instance on every snapshot (no ANALYZE)
    -Step CollectSample   collect the bounded, stratified sample only
    -Step DeriveSample    rebuild labels/features from raw evidence (same command as Derive)
    -Step ValidateSample  validate the sample and write the budget projection
    -Step ApproveFull     record the explicit decision to run the full collection (-BudgetHours)
    -Step CollectBatch    one resumable batch of the full collection (-MaxKeys, optional -MaxMinutes)
    -Step Derive          rebuild labels, features and splits from raw evidence
    -Step Validate        full validation and release gates (PASS, STOP or FAIL)
    -Step Eda             descriptive summaries (no model is trained)
    -Step Freeze          immutable release record after a full PASS
    Nothing here was executed by its author.
.EXAMPLE
    .\scripts\powershell\08_expanded_release.ps1 -Step CollectBatch -MaxKeys 250 -MaxMinutes 120
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('Arithmetic', 'Readiness', 'Manifest', 'PlanCheck', 'CollectSample', 'DeriveSample', 'ValidateSample',
                 'ApproveFull', 'CollectBatch', 'Derive', 'Validate', 'Eda', 'Freeze')]
    [string]$Step,
    [string]$ManifestId,
    [int]$MaxKeys = 0,
    [double]$MaxMinutes = 0,
    [double]$BudgetHours = 0
)

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
$commands = @{
    Arithmetic = 'expanded-arithmetic'; Readiness = 'expanded-readiness'; Manifest = 'expanded-manifest'
    PlanCheck = 'expanded-plan-check'; CollectSample = 'expanded-collect'; DeriveSample = 'expanded-derive'
    ValidateSample = 'expanded-validate'; ApproveFull = 'expanded-approve-full'; CollectBatch = 'expanded-collect'
    Derive = 'expanded-derive'; Validate = 'expanded-validate'; Eda = 'expanded-eda'; Freeze = 'expanded-freeze'
}
$statusName = 'EXPANDED-' + $Step.ToUpper()
try {
    $envValues = Import-DotEnv -RepoRoot $repo
    $python = Get-QcpPython -RepoRoot $repo -EnvValues $envValues
    $needed = @()
    if ($Step -in @('Readiness', 'Manifest', 'PlanCheck', 'CollectSample', 'CollectBatch')) { $needed = @('bench-db', 'evidence-db') }
    elseif ($Step -in @('DeriveSample', 'ValidateSample', 'Derive', 'Validate')) { $needed = @('evidence-db') }
    foreach ($service in $needed) {
        if (-not (Test-ComposeServiceHealthy -Service $service)) { throw "$service is not running or not healthy; run 02_start_databases.ps1 first" }
    }
    $cliArguments = @('-m', 'query_cost_predictor', $commands[$Step])
    if ($ManifestId -and $Step -notin @('Arithmetic', 'Readiness', 'Manifest')) { $cliArguments += @('--manifest-id', $ManifestId) }
    switch ($Step) {
        'CollectSample' { $cliArguments += @('--phase', 'sample') }
        'CollectBatch' {
            if ($MaxKeys -lt 1) { throw 'CollectBatch needs an explicit -MaxKeys (for example 250); full collection always runs in bounded batches' }
            $cliArguments += @('--phase', 'full', '--max-keys', [string]$MaxKeys)
            if ($MaxMinutes -gt 0) { $cliArguments += @('--max-minutes', [string]$MaxMinutes) }
        }
        'ValidateSample' { $cliArguments += @('--scope', 'sample') }
        'Validate' { $cliArguments += @('--scope', 'full') }
        'ApproveFull' {
            if ($BudgetHours -le 0) { throw 'ApproveFull needs -BudgetHours (at least the projected expected hours in reports\expanded\budget_projection_*.md)' }
            $cliArguments += @('--budget-hours', [string]$BudgetHours)
        }
    }
    & $python @cliArguments
    $code = $LASTEXITCODE
    if ($Step -in @('CollectSample', 'CollectBatch')) {
        switch ($code) {
            0 { Write-Host 'All selected keys are determined.' }
            3 { Write-Host 'Batch ended with keys still undetermined (bounded by -MaxKeys/-MaxMinutes or failures). Re-run the same command to continue.' }
            130 { Write-Host 'Interrupted. Re-run the same command to resume; completed modeling_key + repeat_no pairs are skipped.' }
        }
    }
    if ($Step -eq 'Validate' -and $code -eq 4) {
        Write-Host 'STOP: a release gate is not met. Read reports\expanded\validation_full_*.md; do not relabel, pad or freeze.'
    }
    exit $code
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name $statusName -Status 'FAIL'
    exit 1
}
