<#
.SYNOPSIS
    Start bench-db and evidence-db (PostgreSQL 16, bound to 127.0.0.1) and record the resolved image.
.DESCRIPTION
    Validates docker-compose.yml with .env, starts both services, waits for their
    health checks, confirms server_version_num is 16xxxx on both, and writes
    reports/poster/environment/postgres_image.json with the image ID, tags and
    repository digests reported by Docker (never typed by hand).
#>
[CmdletBinding()]
param([int]$HealthTimeoutSeconds = 180)

. (Join-Path $PSScriptRoot '_common.ps1')
$repo = Get-RepoRoot
Set-Location -LiteralPath $repo
try {
    $envValues = Import-DotEnv -RepoRoot $repo
    $image = $envValues['QCP_POSTGRES_IMAGE']
    if ($image -notmatch $script:PostgresImagePattern) { throw "QCP_POSTGRES_IMAGE '$image' is not a PostgreSQL 16 image reference." }

    Invoke-Checked -FilePath 'docker' -Arguments @('compose', 'config', '--quiet') -What 'validate docker-compose.yml with .env'
    Invoke-Checked -FilePath 'docker' -Arguments @('compose', 'up', '-d', 'bench-db', 'evidence-db') -What 'start bench-db and evidence-db'

    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    foreach ($service in @('bench-db', 'evidence-db')) {
        while (-not (Test-ComposeServiceHealthy -Service $service)) {
            if ((Get-Date) -gt $deadline) { throw "$service did not become healthy within $HealthTimeoutSeconds seconds" }
            Start-Sleep -Seconds 3
        }
        Write-Host "$service is healthy"
    }

    $servers = [ordered]@{}
    foreach ($pair in @(@('bench-db', 'QCP_BENCH_ADMIN_USER'), @('evidence-db', 'QCP_EVIDENCE_ADMIN_USER'))) {
        $service = $pair[0]
        $user = $envValues[$pair[1]]
        $num = Invoke-NativeCapture -FilePath 'docker' -Arguments @('compose', 'exec', '-T', $service, 'psql', '-U', $user, '-d', 'postgres', '-Atc', 'SHOW server_version_num')
        $ver = Invoke-NativeCapture -FilePath 'docker' -Arguments @('compose', 'exec', '-T', $service, 'psql', '-U', $user, '-d', 'postgres', '-Atc', 'SHOW server_version')
        if ($num.ExitCode -ne 0 -or $num.Output -notmatch '^16\d{4}$') {
            throw "$service reports server_version_num '$($num.Output)'; PostgreSQL 16 is required"
        }
        $servers[$service] = [ordered]@{ server_version_num = $num.Output; server_version = $ver.Output }
        Write-Host ("{0}: PostgreSQL {1} ({2})" -f $service, $ver.Output, $num.Output)
    }

    $inspect = Invoke-NativeCapture -FilePath 'docker' -Arguments @('image', 'inspect', $image, '--format', '{{json .}}')
    if ($inspect.ExitCode -ne 0) { throw "cannot inspect image ${image}: $($inspect.Output)" }
    $img = $inspect.Output | ConvertFrom-Json
    $dockerServer = Invoke-NativeCapture -FilePath 'docker' -Arguments @('version', '--format', '{{.Server.Version}}')
    $compose = Invoke-NativeCapture -FilePath 'docker' -Arguments @('compose', 'version', '--short')
    $record = [ordered]@{
        captured_at_utc  = (Get-Date).ToUniversalTime().ToString('o')
        configured_image = $image
        image_id         = $img.Id
        repo_tags        = @($img.RepoTags)
        repo_digests     = @($img.RepoDigests)
        image_created    = $img.Created
        servers          = $servers
        docker_server    = $dockerServer.Output
        docker_compose   = $compose.Output
    }
    $out = Join-Path $repo 'reports\poster\environment\postgres_image.json'
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null
    $record | ConvertTo-Json -Depth 6 | Out-File -FilePath $out -Encoding utf8
    Write-Host "Image record written to $out"
    if (@($img.RepoDigests).Count -eq 0) { Write-Warning 'The image has no repository digest; provenance will record NOT_CAPTURED.' }
    Write-FinalStatus -Name 'START-DATABASES' -Status 'PASS'
    exit 0
} catch {
    Write-Host "ERROR: $($_.Exception.Message)"
    Write-FinalStatus -Name 'START-DATABASES' -Status 'FAIL'
    exit 1
}
