<#
.SYNOPSIS
    Read-only environment preflight for the poster evidence milestone.
.DESCRIPTION
    Reports Windows, PowerShell, Python, Docker, Docker Compose and WSL versions,
    memory, CPU, free disk for the repository and QCP_DATA_ROOT, port
    availability, required reference files and - if the image is already
    present - the resolved PostgreSQL image tag and digest.
    It changes nothing on the machine. -OutFile only writes the JSON report.
    It fails clearly instead of choosing another port, path, Python or version.
.EXAMPLE
    .\scripts\powershell\01_preflight.ps1 -OutFile reports\poster\environment\preflight.json
#>
[CmdletBinding()]
param([string]$OutFile)

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
$results = New-Object System.Collections.Generic.List[object]
$facts = [ordered]@{ repository = $repo }

function Add-Result {
    param([string]$Name, [ValidateSet('PASS', 'WARN', 'FAIL', 'INFO')][string]$Status, [string]$Detail)
    $results.Add([pscustomobject]@{ name = $Name; status = $Status; detail = $Detail })
    Write-Host ("[{0}] {1}: {2}" -f $Status, $Name, $Detail)
}

function Convert-SizeToBytes {
    param([string]$Text)
    if ($Text -notmatch '^(\d+(?:\.\d+)?)\s*([kKmMgG]?)[bB]?$') { return $null }
    $number = [double]$Matches[1]
    switch ($Matches[2].ToLower()) { 'k' { return $number * 1KB } 'm' { return $number * 1MB } 'g' { return $number * 1GB } default { return $number } }
}

function Get-FreeGB {
    param([string]$Path)
    $root = [System.IO.Path]::GetPathRoot($Path)
    $drive = New-Object System.IO.DriveInfo($root)
    return [math]::Round($drive.AvailableFreeSpace / 1GB, 2)
}

# --- Operating system, memory, CPU, PowerShell --------------------------------
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $facts.windows = [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber }
    $facts.memory_gb = [ordered]@{ total = [math]::Round($os.TotalVisibleMemorySize / 1MB, 2); free = [math]::Round($os.FreePhysicalMemory / 1MB, 2) }
    Add-Result 'Windows' 'INFO' ("{0} {1} (build {2})" -f $os.Caption, $os.Version, $os.BuildNumber)
    Add-Result 'Memory' 'INFO' ("total {0} GB, free {1} GB" -f $facts.memory_gb.total, $facts.memory_gb.free)
} catch { Add-Result 'Windows/Memory' 'WARN' "could not query: $($_.Exception.Message)" }
try {
    $cpus = @(Get-CimInstance -ClassName Win32_Processor)
    $cores = ($cpus | Measure-Object -Property NumberOfCores -Sum).Sum
    $logical = ($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
    $facts.cpu = [ordered]@{ model = $cpus[0].Name.Trim(); physical_cores = $cores; logical_processors = $logical }
    Add-Result 'CPU' 'INFO' ("{0}; {1} cores / {2} logical" -f $facts.cpu.model, $cores, $logical)
} catch { Add-Result 'CPU' 'WARN' "could not query: $($_.Exception.Message)" }
$facts.powershell = [ordered]@{ version = $PSVersionTable.PSVersion.ToString(); edition = $PSVersionTable.PSEdition }
Add-Result 'PowerShell' 'INFO' ("{0} ({1})" -f $facts.powershell.version, $facts.powershell.edition)
if ($repo -match '\\OneDrive') {
    Add-Result 'Repository location' 'WARN' 'inside OneDrive: .env and .venv are synced; keep QCP_DATA_ROOT outside OneDrive'
} else { Add-Result 'Repository location' 'PASS' $repo }

# --- .env ---------------------------------------------------------------------
$envValues = $null
$envPath = Join-Path $repo '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    Add-Result '.env' 'FAIL' 'missing - copy .env.example to .env (runbook step 3)'
} else {
    try {
        $envValues = Read-DotEnv -Path $envPath
        $required = @('QCP_DATA_ROOT', 'QCP_POSTGRES_IMAGE', 'QCP_DB_HOST', 'QCP_BENCH_PORT', 'QCP_EVIDENCE_PORT', 'QCP_BENCH_CPUS',
                      'QCP_BENCH_MEMORY', 'QCP_BENCH_SHM_SIZE', 'QCP_EVIDENCE_CPUS', 'QCP_EVIDENCE_MEMORY', 'QCP_READER_TEMP_FILE_LIMIT',
                      'QCP_BENCH_ADMIN_USER', 'QCP_EVIDENCE_ADMIN_USER', 'QCP_TPCH_DBGEN_REPO', 'QCP_TPCH_DBGEN_COMMIT') + $script:PasswordVariables
        $missing = @($required | Where-Object { -not $envValues.ContainsKey($_) -or -not $envValues[$_] })
        if ($missing.Count -gt 0) { Add-Result '.env variables' 'FAIL' ("missing: " + ($missing -join ', ')) } else { Add-Result '.env variables' 'PASS' 'all required variables present' }
        $placeholders = @($envValues.Keys | Where-Object { $envValues[$_] -match 'CHANGE_ME' })
        if ($placeholders.Count -gt 0) { Add-Result '.env placeholders' 'FAIL' ("still CHANGE_ME: " + ($placeholders -join ', ')) } else { Add-Result '.env placeholders' 'PASS' 'none left' }
        $badPasswords = @($script:PasswordVariables | Where-Object { $envValues.ContainsKey($_) -and $envValues[$_] -notmatch '^[A-Za-z0-9]{16,}$' })
        if ($badPasswords.Count -gt 0) { Add-Result 'Password format' 'FAIL' ("need 16+ letters/digits: " + ($badPasswords -join ', ')) } else { Add-Result 'Password format' 'PASS' 'letters/digits, 16+ characters (values not shown)' }
        if ($envValues['QCP_DB_HOST'] -notin @('127.0.0.1', 'localhost')) { Add-Result 'Database host' 'FAIL' 'QCP_DB_HOST must be 127.0.0.1 or localhost' } else { Add-Result 'Database host' 'PASS' $envValues['QCP_DB_HOST'] }
        $image = $envValues['QCP_POSTGRES_IMAGE']
        if ($image -match $script:PostgresImagePattern) { Add-Result 'PostgreSQL image' 'PASS' "$image (PostgreSQL 16)" } else { Add-Result 'PostgreSQL image' 'FAIL' "$image is not a postgres:16 image reference" }
    } catch { Add-Result '.env' 'FAIL' $_.Exception.Message; $envValues = $null }
}

# --- QCP_DATA_ROOT and free space ------------------------------------------------
Add-Result 'Free space (repository drive)' $(if ((Get-FreeGB $repo) -ge 2) { 'PASS' } else { 'WARN' }) ("{0} GB free" -f (Get-FreeGB $repo))
if ($envValues -and $envValues['QCP_DATA_ROOT']) {
    $dataRoot = $envValues['QCP_DATA_ROOT']
    if (-not [System.IO.Path]::IsPathRooted($dataRoot)) {
        Add-Result 'QCP_DATA_ROOT' 'FAIL' "must be an absolute path: $dataRoot"
    } elseif (-not (Test-Path -LiteralPath $dataRoot -PathType Container)) {
        Add-Result 'QCP_DATA_ROOT' 'FAIL' "does not exist: $dataRoot (create it yourself, runbook step 3)"
    } else {
        $resolved = (Resolve-Path -LiteralPath $dataRoot).Path
        if ($resolved.TrimEnd('\').StartsWith($repo.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) {
            Add-Result 'QCP_DATA_ROOT' 'FAIL' "must be outside the repository: $resolved"
        } else {
            $status = 'PASS'; if ($resolved -match '\\OneDrive') { $status = 'WARN' }
            Add-Result 'QCP_DATA_ROOT' $status $resolved
            $free = Get-FreeGB $resolved
            $facts.data_root = [ordered]@{ path = $resolved; free_gb = $free }
            Add-Result 'Free space (QCP_DATA_ROOT drive)' $(if ($free -ge 10) { 'PASS' } elseif ($free -ge 2) { 'WARN' } else { 'FAIL' }) ("{0} GB free" -f $free)
        }
    }
}
$systemFree = Get-FreeGB ($env:SystemDrive + '\')
$facts.system_drive_free_gb = $systemFree
Add-Result 'Free space (system drive; default Docker Desktop disk location)' $(if ($systemFree -ge 20) { 'PASS' } else { 'WARN' }) ("{0} GB free" -f $systemFree)

# --- Python --------------------------------------------------------------------
try {
    $python = Get-QcpPython -RepoRoot $repo -EnvValues $envValues
    # No double quotes inside native arguments: Windows PowerShell 5.1 does not escape them.
    $version = Invoke-NativeCapture -FilePath $python -Arguments @('-c', 'import sys; print(sys.version.split()[0])')
    $facts.python = [ordered]@{ path = $python; version = $version.Output }
    $parts = $version.Output.Split('.')
    if ($version.ExitCode -eq 0 -and [int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { Add-Result 'Python' 'PASS' ("{0} at {1}" -f $version.Output, $python) } else { Add-Result 'Python' 'FAIL' ("need 3.11+, got '{0}'" -f $version.Output) }
    $package = Invoke-NativeCapture -FilePath $python -Arguments @('-c', 'import query_cost_predictor as q; print(q.__version__)')
    if ($package.ExitCode -eq 0) { Add-Result 'Package installed' 'PASS' "query_cost_predictor $($package.Output)" } else { Add-Result 'Package installed' 'FAIL' 'run runbook step 2 (pip install -e ".[notebook,dev]")' }
} catch { Add-Result 'Python' 'FAIL' $_.Exception.Message }

# --- Docker, Compose, WSL -------------------------------------------------------------
$client = Invoke-NativeCapture -FilePath 'docker' -Arguments @('version', '--format', '{{.Client.Version}}')
$server = Invoke-NativeCapture -FilePath 'docker' -Arguments @('version', '--format', '{{.Server.Version}}')
$dockerOk = $server.ExitCode -eq 0
if ($dockerOk) { Add-Result 'Docker' 'PASS' ("client {0}, server {1}" -f $client.Output, $server.Output) } else { Add-Result 'Docker' 'FAIL' 'Docker Desktop is not running or not installed' }
$facts.docker = [ordered]@{ client = $client.Output; server = $server.Output }
$compose = Invoke-NativeCapture -FilePath 'docker' -Arguments @('compose', 'version', '--short')
if ($compose.ExitCode -eq 0) { Add-Result 'Docker Compose' 'PASS' $compose.Output } else { Add-Result 'Docker Compose' 'FAIL' 'docker compose v2 not available' }
$facts.docker.compose = $compose.Output
$env:WSL_UTF8 = '1'
$wsl = Invoke-NativeCapture -FilePath 'wsl.exe' -Arguments @('--version')
if ($wsl.ExitCode -eq 0 -and $wsl.Output) { Add-Result 'WSL' 'INFO' (($wsl.Output -split "`n")[0..2] -join '; ') } else { Add-Result 'WSL' 'WARN' 'wsl --version unavailable (older WSL or not installed)' }
$facts.wsl = $wsl.Output

if ($dockerOk) {
    $info = Invoke-NativeCapture -FilePath 'docker' -Arguments @('info', '--format', '{{json .}}')
    if ($info.ExitCode -eq 0) {
        $docker = $info.Output | ConvertFrom-Json
        $facts.docker.ncpu = $docker.NCPU
        $facts.docker.mem_total_gb = [math]::Round($docker.MemTotal / 1GB, 2)
        $facts.docker.operating_system = $docker.OperatingSystem
        Add-Result 'Docker resources' 'INFO' ("{0} CPUs, {1} GB memory ({2})" -f $docker.NCPU, $facts.docker.mem_total_gb, $docker.OperatingSystem)
        if ($envValues) {
            $benchCpus = [double]$envValues['QCP_BENCH_CPUS']
            if ($benchCpus -gt $docker.NCPU) { Add-Result 'QCP_BENCH_CPUS' 'FAIL' "$benchCpus exceeds Docker's $($docker.NCPU) CPUs" } else { Add-Result 'QCP_BENCH_CPUS' 'PASS' "$benchCpus of $($docker.NCPU)" }
            $memory = (Convert-SizeToBytes $envValues['QCP_BENCH_MEMORY']) + (Convert-SizeToBytes $envValues['QCP_EVIDENCE_MEMORY'])
            if ($memory -gt $docker.MemTotal) { Add-Result 'Container memory' 'FAIL' ("bench+evidence {0} GB exceed Docker's {1} GB" -f [math]::Round($memory / 1GB, 2), $facts.docker.mem_total_gb) } else { Add-Result 'Container memory' 'PASS' ("bench+evidence {0} GB of {1} GB" -f [math]::Round($memory / 1GB, 2), $facts.docker.mem_total_gb) }
        }
    }
}

# --- Ports ------------------------------------------------------------------------
if ($envValues) {
    foreach ($pair in @(@('bench-db', 'QCP_BENCH_PORT'), @('evidence-db', 'QCP_EVIDENCE_PORT'))) {
        $service = $pair[0]; $port = [int]$envValues[$pair[1]]
        try {
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
        } catch {
            Add-Result "Port $port ($service)" 'WARN' "could not query listeners: $($_.Exception.Message)"
            continue
        }
        if ($listeners.Count -eq 0) { Add-Result "Port $port ($service)" 'PASS' 'free'; continue }
        $published = Invoke-NativeCapture -FilePath 'docker' -Arguments @('compose', 'port', $service, '5432')
        if ($published.ExitCode -eq 0 -and $published.Output -match ":$port$") {
            Add-Result "Port $port ($service)" 'PASS' 'in use by this project''s running container'
        } else {
            $owners = ($listeners | ForEach-Object { $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; if ($p) { "$($p.ProcessName)($($_.OwningProcess))" } else { "pid $($_.OwningProcess)" } }) -join ', '
            Add-Result "Port $port ($service)" 'FAIL' "already in use by $owners; choose another port in .env (no automatic change)"
        }
    }
}

# --- Required reference and configuration files -------------------------------------
$requiredFiles = @(
    'reference_pilot\output\query_runs.jsonl', 'reference_pilot\output\query_dataset.csv',
    'reference_pilot\output\validation_summary.json', 'reference_pilot\output\errors.jsonl',
    'reference_pilot\output\sql_validation.json', 'config\feature_registry.yaml', 'config\pilot_reported_claims.yaml',
    'config\poster_smoke.yaml', 'config\poster_claims.yaml', 'docker\tpch-tools\Dockerfile'
)
$missingFiles = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $repo $_)) })
if ($missingFiles.Count -eq 0) { Add-Result 'Required files' 'PASS' "$($requiredFiles.Count) present" } else { Add-Result 'Required files' 'FAIL' ("missing: " + ($missingFiles -join ', ')) }
$docs = @(Get-ChildItem -LiteralPath (Join-Path $repo 'reference_docs') -File -ErrorAction SilentlyContinue)
Add-Result 'Reference documents' $(if ($docs.Count -ge 5) { 'PASS' } else { 'WARN' }) "$($docs.Count) files in reference_docs"

# --- Resolved PostgreSQL image (only if already present locally) ----------------------------
$facts.postgres_image = 'NOT_AVAILABLE_YET'
if ($dockerOk -and $envValues -and $envValues['QCP_POSTGRES_IMAGE']) {
    $inspect = Invoke-NativeCapture -FilePath 'docker' -Arguments @('image', 'inspect', $envValues['QCP_POSTGRES_IMAGE'], '--format', '{{json .}}')
    if ($inspect.ExitCode -eq 0) {
        $img = $inspect.Output | ConvertFrom-Json
        $facts.postgres_image = [ordered]@{ configured = $envValues['QCP_POSTGRES_IMAGE']; id = $img.Id; repo_tags = $img.RepoTags; repo_digests = $img.RepoDigests }
        Add-Result 'PostgreSQL image (resolved)' 'INFO' ("{0}; digests: {1}" -f $img.Id, ($img.RepoDigests -join ', '))
    } else {
        Add-Result 'PostgreSQL image (resolved)' 'INFO' 'NOT_AVAILABLE_YET (image not pulled; step 5 pulls and records it)'
    }
}

$failCount = @($results | Where-Object { $_.status -eq 'FAIL' }).Count
$overall = $(if ($failCount -gt 0) { 'FAIL' } else { 'PASS' })
if ($OutFile) {
    $target = $(if ([System.IO.Path]::IsPathRooted($OutFile)) { $OutFile } else { Join-Path $repo $OutFile })
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    [ordered]@{ created_at_utc = (Get-Date).ToUniversalTime().ToString('o'); status = $overall; facts = $facts; results = $results } |
        ConvertTo-Json -Depth 8 | Out-File -FilePath $target -Encoding utf8
    Write-Host "Report written to $target"
}
Write-FinalStatus -Name 'PREFLIGHT' -Status $overall
if ($failCount -gt 0) { exit 1 } else { exit 0 }
