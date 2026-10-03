# Shared helpers for the poster-evidence PowerShell scripts. Dot-source only:
#   . (Join-Path $PSScriptRoot '_common.ps1')
# Compatible with Windows PowerShell 5.1 and PowerShell 7.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PasswordVariables = @(
    'QCP_BENCH_ADMIN_PASSWORD', 'QCP_EVIDENCE_ADMIN_PASSWORD',
    'QCP_BENCH_OWNER_PASSWORD', 'QCP_BENCH_READER_PASSWORD',
    'QCP_EVIDENCE_OWNER_PASSWORD', 'QCP_EVIDENCE_WRITER_PASSWORD'
)
$script:PostgresImagePattern = '^postgres:16(\.\d+)?(-[A-Za-z0-9.]+)?(@sha256:[0-9a-f]{64})?$'

function Get-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}

function Read-DotEnv {
    param([Parameter(Mandatory)][string]$Path)
    $values = @{}
    foreach ($raw in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $raw.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { continue }
        if ($line.StartsWith('export ')) { $line = $line.Substring(7).TrimStart() }
        $idx = $line.IndexOf('=')
        if ($idx -lt 1) { throw "Malformed .env line (expected KEY=VALUE): $raw" }
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1).Trim()
        $quoted = $value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))
        if ($quoted) {
            $value = $value.Substring(1, $value.Length - 2)
        } elseif ($value.Contains(' #')) {
            $value = $value.Substring(0, $value.IndexOf(' #')).TrimEnd()
        }
        $values[$key] = $value
    }
    return $values
}

function Import-DotEnv {
    param([Parameter(Mandatory)][string]$RepoRoot)
    $envPath = Join-Path $RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw ".env not found at '$envPath'. Copy .env.example to .env and fill it in (runbook step 3)."
    }
    $values = Read-DotEnv -Path $envPath
    foreach ($key in $values.Keys) {
        if (-not [Environment]::GetEnvironmentVariable($key, 'Process')) {
            [Environment]::SetEnvironmentVariable($key, $values[$key], 'Process')
        }
    }
    return $values
}

function Get-QcpPython {
    param([Parameter(Mandatory)][string]$RepoRoot, [hashtable]$EnvValues)
    $python = $null
    if ($EnvValues -and $EnvValues.ContainsKey('QCP_PYTHON') -and $EnvValues['QCP_PYTHON']) { $python = $EnvValues['QCP_PYTHON'] }
    if (-not $python) { $python = '.venv\Scripts\python.exe' }
    if (-not [System.IO.Path]::IsPathRooted($python)) { $python = Join-Path $RepoRoot $python }
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python interpreter not found at '$python' (QCP_PYTHON). Create the virtual environment first (runbook step 1). No other interpreter is used."
    }
    return $python
}

function Invoke-Checked {
    # Run a native command, stream its output, and throw if the exit code is not 0.
    param([Parameter(Mandatory)][string]$FilePath, [string[]]$Arguments = @(), [Parameter(Mandatory)][string]$What)
    Write-Host ">> $What"
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

function Invoke-NativeCapture {
    # Run a native command and capture stdout+stderr without letting stderr abort
    # the script (Windows PowerShell 5.1 turns redirected stderr into errors).
    param([Parameter(Mandatory)][string]$FilePath, [string[]]$Arguments = @())
    if (-not (Get-Command $FilePath -ErrorAction SilentlyContinue)) {
        return [pscustomobject]@{ ExitCode = -1; Output = "command not found: $FilePath" }
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $FilePath @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    $text = (@($output) | ForEach-Object { $_.ToString() }) -join "`n"
    return [pscustomobject]@{ ExitCode = $code; Output = ($text -replace "`0", '').Trim() }
}

function Test-ComposeServiceHealthy {
    param([Parameter(Mandatory)][string]$Service)
    $id = Invoke-NativeCapture -FilePath 'docker' -Arguments @('compose', 'ps', '-q', $Service)
    if ($id.ExitCode -ne 0 -or -not $id.Output) { return $false }
    $health = Invoke-NativeCapture -FilePath 'docker' -Arguments @('inspect', '--format', '{{.State.Health.Status}}', $id.Output.Split("`n")[0].Trim())
    return ($health.ExitCode -eq 0 -and $health.Output -eq 'healthy')
}

function Write-FinalStatus {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Status)
    Write-Host ''
    Write-Host ("{0}: {1}" -f $Name, $Status)
}
