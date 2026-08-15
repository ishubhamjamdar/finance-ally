<#
.SYNOPSIS
    Stop FinAlly (Windows PowerShell). PLAN.md §11.

.DESCRIPTION
    Stops and removes the container. **Never removes the volume** — the
    portfolio, the watchlist and the chat history live in `finally-data`, and
    the whole point of the named volume is that start/stop/start leaves them
    intact. Removing it is a deliberate act:

        docker volume rm finally-data

    Idempotent: stopping something that is not running is a success.

.EXAMPLE
    .\scripts\stop_windows.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# Overridable for the same reason as in start_windows.ps1: a smoke test stops
# its own container, never yours.
$Container = if ($env:FINALLY_CONTAINER) { $env:FINALLY_CONTAINER } else { "finally" }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker is not installed - nothing to stop."
    exit 0
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "The Docker daemon is not running - nothing to stop."
    exit 0
}

$existing = docker ps --all --quiet --filter "name=^/$Container$"
if (-not $existing) {
    Write-Host "FinAlly is not running."
    exit 0
}

# `docker stop` sends SIGTERM first, which uvicorn - PID 1 by the Dockerfile's
# exec-form CMD - turns into the lifespan shutdown: the snapshot task is
# cancelled and awaited, and the market source is stopped.
Write-Host "Stopping $Container..."
docker stop $Container | Out-Null
docker rm $Container | Out-Null

Write-Host "Stopped. Your portfolio is preserved in the 'finally-data' volume."
Write-Host "Start it again with .\scripts\start_windows.ps1"
