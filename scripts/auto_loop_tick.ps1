# auto_loop_tick.ps1 - fired by Windows Task Scheduler for unattended runs.
# Generalizes WAT Stage 6b: each tick asks the LOCAL app to start a batch for each
# configured candidate. The app's orchestrator enforces the lock/queue/cap contract,
# so a tick that arrives mid-batch is safely queued, not duplicated.
#
# This needs the local app server running (python -m app.main). If the server is
# down, the tick logs and exits cleanly - it never launches a browser itself.
#
# Configure candidates + mode via the env vars below or edit the defaults.

$ErrorActionPreference = "Continue"
$Root      = Split-Path -Parent $PSScriptRoot
$LogFile   = Join-Path $Root "data\auto_loop_tick.log"
$BaseUrl   = if ($env:JOBAPPLY_URL) { $env:JOBAPPLY_URL } else { "http://127.0.0.1:8765" }
$Mode      = if ($env:JOBAPPLY_MODE) { $env:JOBAPPLY_MODE } else { "live" }
$Candidates = if ($env:JOBAPPLY_CANDIDATES) { $env:JOBAPPLY_CANDIDATES -split "," } else { @("racheal","gabriel") }

function Write-TickLog($msg) {
    $stamp = (Get-Date).ToString("o")
    Add-Content -Path $LogFile -Value "$stamp $msg" -Encoding utf8
}

New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null
Write-TickLog "tick fired (mode=$Mode, candidates=$($Candidates -join ','))"

# Is the local app up?
try {
    $null = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 5
} catch {
    Write-TickLog "app server not reachable at $BaseUrl - skipping this tick. ($($_.Exception.Message))"
    exit 0
}

foreach ($c in $Candidates) {
    try {
        $body = @{ candidate = $c.Trim(); mode = $Mode } | ConvertTo-Json
        $r = Invoke-RestMethod -Uri "$BaseUrl/api/runs/start" -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15
        Write-TickLog "start $($c): started=$($r.started) reason=$($r.reason)"
    } catch {
        Write-TickLog "start $($c) failed: $($_.Exception.Message)"
    }
}

Write-TickLog "tick complete"
exit 0
