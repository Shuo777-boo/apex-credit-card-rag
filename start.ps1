#Requires -Version 5.1
<#
Start the containerized demo. Does not install Docker or run the paid evaluation suite.
Usage: powershell -ExecutionPolicy Bypass -File .\start.ps1
       pwsh -File .\start.ps1 -SkipBootstrap -TimeoutSeconds 300
#>
[CmdletBinding()]
param(
    [switch]$SkipBootstrap,
    [switch]$NoBuild,
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 180,
    [string]$FrontendUrl = 'http://127.0.0.1:8080',
    [string]$BackendUrl = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskOldLocation = Get-Location
$taskOldComposeEnvFiles = $env:COMPOSE_ENV_FILES

function Invoke-TaskDocker {
    param([string[]]$DockerArgs)
    & docker @DockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed (exit $LASTEXITCODE). Check the output above."
    }
}

try {
    Set-Location -LiteralPath $taskRoot
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Docker is missing. Install/start Docker Desktop with Linux containers; this script does not install it.'
    }
    $taskEnvFile = if ($env:APEX_ENV_FILE) { $env:APEX_ENV_FILE } else { '.env' }
    foreach ($taskFile in @('docker-compose.yml', $taskEnvFile)) {
        if (-not (Test-Path -LiteralPath (Join-Path $taskRoot $taskFile) -PathType Leaf)) {
            throw "Missing $taskFile. Create .env from .env.example and configure database/model credentials. See docs/deployment.md."
        }
    }
    Invoke-TaskDocker -DockerArgs @('info', '--format', '{{.ServerVersion}}')
    Invoke-TaskDocker -DockerArgs @('compose', 'version')
    # Compose reads the selected file for interpolation and service env_file consistently.
    $env:COMPOSE_ENV_FILES = $taskEnvFile
    Invoke-TaskDocker -DockerArgs @('compose', '--profile', 'app', '--profile', 'tools', 'config', '--quiet')
    # Read only nonsecret port mappings; never echo the expanded Compose environment.
    $taskEnvLines = Get-Content -LiteralPath $taskEnvFile
    if (-not $PSBoundParameters.ContainsKey('BackendUrl')) {
        $taskBackendPort = $env:BACKEND_PORT
        if (-not $taskBackendPort) {
            $taskPortLine = $taskEnvLines | Where-Object { $_ -match '^BACKEND_PORT=([0-9]+)$' } | Select-Object -Last 1
            if ($taskPortLine) { $taskBackendPort = ($taskPortLine -split '=', 2)[1] }
        }
        if ($taskBackendPort) { $BackendUrl = 'http://127.0.0.1:' + $taskBackendPort }
    }
    if (-not $PSBoundParameters.ContainsKey('FrontendUrl')) {
        $taskFrontendPort = $env:FRONTEND_PORT
        if (-not $taskFrontendPort) {
            $taskPortLine = $taskEnvLines | Where-Object { $_ -match '^FRONTEND_PORT=([0-9]+)$' } | Select-Object -Last 1
            if ($taskPortLine) { $taskFrontendPort = ($taskPortLine -split '=', 2)[1] }
        }
        if ($taskFrontendPort) { $FrontendUrl = 'http://127.0.0.1:' + $taskFrontendPort }
    }
    Write-Host '[1/4] Starting PostgreSQL...'
    Invoke-TaskDocker -DockerArgs @('compose', 'up', '-d', '--wait', '--wait-timeout', "$TimeoutSeconds", 'postgres')

    if (-not $SkipBootstrap) {
        Write-Host '[2/4] Preparing demo data and indexes (Embedding API may incur costs)...'
        $taskBootstrapArgs = @('compose', '--profile', 'tools', 'run', '--rm')
        if (-not $NoBuild) { $taskBootstrapArgs += '--build' }
        Invoke-TaskDocker -DockerArgs ($taskBootstrapArgs + @('bootstrap'))
    } else {
        Write-Host '[2/4] Skipping bootstrap; an active index must already exist.'
    }

    Write-Host '[3/4] Starting backend/frontend...'
    $taskAppArgs = @('compose', '--profile', 'app', 'up', '-d', '--wait', '--wait-timeout', "$TimeoutSeconds")
    $taskAppArgs += $(if ($NoBuild) { '--no-build' } else { '--build' })
    Invoke-TaskDocker -DockerArgs $taskAppArgs
    Write-Host '[4/4] Checking application readiness...'
    $taskReadyUrl = $BackendUrl.TrimEnd('/') + '/health/ready'
    $taskDeadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $taskReady = $false
    do {
        try {
            $taskResponse = Invoke-WebRequest -Uri $taskReadyUrl -UseBasicParsing -TimeoutSec 5
            $taskFrontend = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 5
            if ($taskResponse.StatusCode -eq 200 -and $taskFrontend.StatusCode -eq 200) {
                $taskReady = $true
                break
            }
        } catch {
            # Retry only readiness probes; model requests are handled by the application.
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $taskDeadline)
    if (-not $taskReady) {
        throw "Readiness timed out. Inspect: docker compose --profile app logs --tail 100 backend frontend"
    }
    Write-Host "Ready: $FrontendUrl"
    Write-Host ('Swagger: ' + $BackendUrl.TrimEnd('/') + '/docs')
    Write-Host 'Stop: docker compose --profile app stop'
    Write-Host 'Run evaluations separately; startup does not run Judge or the benchmark suite.'
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    $env:COMPOSE_ENV_FILES = $taskOldComposeEnvFiles
    Set-Location -LiteralPath $taskOldLocation.Path
}
