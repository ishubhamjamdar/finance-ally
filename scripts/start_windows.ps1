<#
.SYNOPSIS
    Launch FinAlly (Windows PowerShell). PLAN.md §11.

.DESCRIPTION
    The PowerShell twin of scripts/start_mac.sh, and deliberately the same
    deployment: same image, container, volume and port, so a portfolio built on
    one machine is the one that comes back on the other.

    Idempotent — running it twice does nothing the second time but print the URL.
    The container port is always 8000; FINALLY_PORT changes only the host side.

.PARAMETER Build
    Force a rebuild of the image and recreate the container on it.

.PARAMETER NoOpen
    Do not open a browser (CI, smoke tests).

.EXAMPLE
    .\scripts\start_windows.ps1
    .\scripts\start_windows.ps1 -Build
    $env:FINALLY_PORT = "8010"; .\scripts\start_windows.ps1
#>
[CmdletBinding()]
param(
    [switch]$Build,
    [switch]$NoOpen
)

# Stop on the first error from a cmdlet. Native commands (docker) set
# $LASTEXITCODE instead, which is checked where it matters.
$ErrorActionPreference = "Stop"

# The defaults are the deployment: docker-compose.yml names the same image,
# container, volume and port, so the two front doors share one database. The
# overrides exist so a smoke test can exercise this script without stopping the
# instance you are actually using.
$Image     = if ($env:FINALLY_IMAGE)     { $env:FINALLY_IMAGE }     else { "finally:latest" }
$Container = if ($env:FINALLY_CONTAINER) { $env:FINALLY_CONTAINER } else { "finally" }
$Volume    = if ($env:FINALLY_VOLUME)    { $env:FINALLY_VOLUME }    else { "finally-data" }
$Port      = if ($env:FINALLY_PORT)      { $env:FINALLY_PORT }      else { "8000" }
# Loopback, not 0.0.0.0. `docker run -p 8000:8000` publishes on every interface,
# and FinAlly has no login by design (PLAN.md §2) - so on a shared network that
# would hand anyone the portfolio, the watchlist, and a POST /api/chat that
# spends *your* OpenRouter credits. This is a localhost app; bind it there.
# $env:FINALLY_BIND = "0.0.0.0" for the deliberate case of reaching it remotely.
$Bind      = if ($env:FINALLY_BIND)      { $env:FINALLY_BIND }      else { "127.0.0.1" }
# How long `docker stop` waits for SIGTERM to be honoured before SIGKILL. The
# container declares it, because the host default is not what it is usually
# said to be: measured on Docker 29, it is about a second, and the app asks
# uvicorn for up to 3 to close an open price stream. Without this the lifespan
# is killed mid-shutdown - the snapshot task never awaited, the source never
# stopped.
$StopTimeout = 15
# Derived from this script's own location: the build context is the repo root,
# and `docker build` from anywhere else builds the wrong thing.
$Repo      = Split-Path -Parent $PSScriptRoot
$EnvFile   = Join-Path $Repo ".env"

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Error "Docker is not installed. Install Docker Desktop: https://docker.com/get-started"
    }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker is installed but the daemon is not running. Start Docker Desktop and try again."
    }
}

Test-Docker

# An absent .env is a working configuration — simulated prices, no AI chat — so
# this warns rather than failing. PLAN.md §5: every other variable defaults.
if (-not (Test-Path $EnvFile)) {
    Write-Host "Note: no .env found at $EnvFile."
    Write-Host "      The app will run on simulated market data; the AI chat needs OPENROUTER_API_KEY."
    Write-Host "      Copy .env.example to .env and add your key to enable it."
}

$running = docker ps --quiet --filter "name=^/$Container$"
if ($running -and -not $Build) {
    # Report the port it is actually published on, not the one we would have
    # chosen — they differ if FINALLY_PORT changed since it was started.
    $published = docker port $Container 8000/tcp 2>$null | Select-Object -First 1
    $actual = if ($published) { $published.Split(":")[-1] } else { $Port }
    Write-Host "FinAlly is already running at http://localhost:$actual"
    exit 0
}

$imageId = docker images --quiet $Image
if ($Build -or -not $imageId) {
    Write-Host "Building $Image..."
    docker build -t $Image $Repo
    if ($LASTEXITCODE -ne 0) { Write-Error "docker build failed." }
}

$existing = docker ps --all --quiet --filter "name=^/$Container$"
if ($existing) {
    if ($Build) {
        # A rebuilt image is only reached by recreating the container. The
        # volume is untouched, so the portfolio survives.
        Write-Host "Recreating $Container on the new image..."
        docker rm --force $Container | Out-Null
    }
    else {
        Write-Host "Starting the existing $Container container..."
        docker start $Container | Out-Null
    }
}

if (-not (docker ps --quiet --filter "name=^/$Container$")) {
    # Only a listener that is not ours is a conflict; our own container was
    # handled above. Get-NetTCPConnection is absent on some hosts, so a failure
    # to check is not a failure to start.
    try {
        $inUse = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
    }
    catch {
        $inUse = $null
    }
    if ($inUse) {
        Write-Error "Port $Port is already in use by another process. Free it, or choose another: `$env:FINALLY_PORT = '8010'"
    }

    $runArgs = @(
        "run", "--detach",
        "--name", $Container,
        "--publish", "${Bind}:${Port}:8000",
        "--volume", "${Volume}:/app/db",
        "--restart", "unless-stopped",
        "--stop-timeout", "$StopTimeout"
    )
    if (Test-Path $EnvFile) { $runArgs += @("--env-file", $EnvFile) }
    $runArgs += $Image

    Write-Host "Starting $Container on port $Port..."
    docker @runArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "docker run failed." }
}

# A container that already existed keeps the mapping it was created with, and
# `docker start` cannot change it. Ask it which port it actually publishes,
# rather than polling the one we would have chosen and reporting a healthy app
# as a 60-second timeout.
$publishedLine = docker port $Container 8000/tcp 2>$null | Select-Object -First 1
if ($publishedLine) {
    $published = $publishedLine.Split(":")[-1]
    if ($published -and $published -ne $Port) {
        Write-Host "Note: the existing container publishes $published, not $Port."
        Write-Host "      To move it: .\scripts\stop_windows.ps1, then start again."
        $Port = $published
    }
}

$Url = "http://localhost:$Port"
Write-Host -NoNewline "Waiting for FinAlly to come up"
foreach ($attempt in 1..60) {
    try {
        $response = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host ""
            Write-Host "FinAlly is running at $Url"
            if (-not $NoOpen) { Start-Process $Url }
            Write-Host "Stop it with .\scripts\stop_windows.ps1"
            exit 0
        }
    }
    catch {
        # Not up yet. The loop is the retry.
    }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "FinAlly did not become healthy within 60s. Recent logs:"
docker logs --tail 30 $Container
exit 1
