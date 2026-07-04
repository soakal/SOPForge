#Requires -Version 5.1
<#
.SYNOPSIS
    Watchdog for the SOPForge autonomous build loop.

.DESCRIPTION
    Launches `claude /goal <phase>` and relaunches with --continue whenever the
    session exits before the phase results file appears — which is what a Pro/Max
    usage-limit interrupt looks like from the outside. Backs off between restarts
    so a hard failure doesn't hot-loop. Replaces the NEXUS dollar-ceiling hook,
    which is meaningless on subscription billing.

    Stop it any time by creating a STOP file in the repo root:  ni STOP

.PARAMETER Phase
    Phase to run: 1, 2, 3, or "all" (default).

.PARAMETER MaxRestarts
    Give up after this many relaunches (default 40 — roughly a week of 5-hour
    windows; raise it if you expect a long run).

.EXAMPLE
    .\run-loop.ps1                 # run all phases
.EXAMPLE
    .\run-loop.ps1 -Phase 1        # phase 1 only
#>
[CmdletBinding()]
param(
    [ValidateSet('1','2','3','all')]
    [string]$Phase = 'all',

    [ValidateRange(1, 500)]
    [int]$MaxRestarts = 40,

    [string]$LogPath = "$PSScriptRoot\loop-$(Get-Date -Format 'yyyyMMdd-HHmmss').log"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Log {
    param([Parameter(Mandatory)][string]$Message,
          [ValidateSet('INFO','WARN','ERROR','SUCCESS')][string]$Level = 'INFO')
    $entry = "[{0}] [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    switch ($Level) {
        'ERROR'   { Write-Host $entry -ForegroundColor Red }
        'WARN'    { Write-Host $entry -ForegroundColor Yellow }
        'SUCCESS' { Write-Host $entry -ForegroundColor Green }
        default   { Write-Host $entry }
    }
    Add-Content -Path $LogPath -Value $entry -ErrorAction SilentlyContinue
}

function Test-PhaseDone {
    param([string]$P)
    if ($P -eq 'all') {
        return (Test-Path "$PSScriptRoot\phases\03-results.md")
    }
    return (Test-Path ("$PSScriptRoot\phases\{0:D2}-results.md" -f [int]$P))
}

# ── Preflight ──────────────────────────────────────────────
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Log "claude CLI not found in PATH." 'ERROR'; exit 3
}
$apiKeyUser = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User')
$apiKeyMachine = [Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'Machine')
if ($apiKeyUser -or $apiKeyMachine -or $env:ANTHROPIC_API_KEY) {
    Write-Log "ANTHROPIC_API_KEY is set — Claude Code will bill the API instead of your subscription. Unset it or accept the billing." 'WARN'
}

Set-Location $PSScriptRoot
Write-Log "=== SOPForge loop start — phase: $Phase, log: $LogPath ==="

# ── Main loop ──────────────────────────────────────────────
$restarts = 0
$firstRun = $true

while ($true) {
    if (Test-Path "$PSScriptRoot\STOP") {
        Write-Log "STOP file found — exiting cleanly." 'WARN'; exit 0
    }
    if (Test-PhaseDone $Phase) {
        Write-Log "Phase results file present — build complete." 'SUCCESS'; exit 0
    }
    if ($restarts -ge $MaxRestarts) {
        Write-Log "Hit MaxRestarts ($MaxRestarts) without completion — giving up." 'ERROR'; exit 1
    }

    if ($firstRun) {
        Write-Log "Launching: claude /goal $Phase"
        & claude --permission-mode acceptEdits -p "/goal $Phase"
        $firstRun = $false
    }
    else {
        Write-Log "Relaunching with --continue (restart #$restarts)"
        & claude --permission-mode acceptEdits --continue -p "Resume the /goal loop exactly where the committed task files say you left off. Re-read CLAUDE.md first."
    }
    $code = $LASTEXITCODE
    $restarts++

    if (Test-PhaseDone $Phase) {
        Write-Log "Build complete after $restarts session(s)." 'SUCCESS'; exit 0
    }

    # Session ended without completion: usage limit, crash, or escalation.
    # Escalations write ESCALATION.md — surface those and stop.
    if (Test-Path "$PSScriptRoot\ESCALATION.md") {
        Write-Log "Loop escalated to human — see ESCALATION.md. Stopping." 'ERROR'
        Get-Content "$PSScriptRoot\ESCALATION.md" | ForEach-Object { Write-Log $_ 'ERROR' }
        exit 1
    }

    $waitMin = [Math]::Min(5 * $restarts, 65)   # linear backoff, capped past a limit window
    Write-Log "Session exited (code $code) without completion. Waiting $waitMin min before relaunch." 'WARN'
    Start-Sleep -Seconds ($waitMin * 60)
}
