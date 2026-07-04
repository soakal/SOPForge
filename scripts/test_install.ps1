<#
.SYNOPSIS
    Automated install/uninstall round-trip (AC4): snapshots directory
    state, installs to a temp path on a non-default port, polls the
    health endpoint, uninstalls, and asserts before/after directory state
    matches. Separately probes the -Autostart branch: creates the
    scheduled task, confirms it via Get-ScheduledTask, then removes it.

.DESCRIPTION
    If scheduled-task creation fails (e.g. elevation/policy blocks
    ONLOGON-triggered task registration for the current user), this
    records the failure and exits non-zero -- per CLAUDE.md prime
    directive 1, an escalation-worthy blocker is never silently skipped
    or treated as a pass.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$InstallScript = Join-Path $RepoRoot "install.ps1"
$UninstallScript = Join-Path $RepoRoot "uninstall.ps1"

function Wait-ForHealthy($Port, $TimeoutSeconds = 15) {
    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        try {
            $Response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Stop-ServerCleanly($Proc, $Port) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port/shutdown" -Method POST -UseBasicParsing -TimeoutSec 2 | Out-Null
    } catch {}
    $Proc.WaitForExit(10000) | Out-Null
    if (-not $Proc.HasExited) {
        Stop-Process -Id $Proc.Id -Force -ErrorAction SilentlyContinue
    }
}

# --- Round trip 1: plain install, no autostart ---
$TestRoot = Join-Path $env:TEMP "sopforge-install-test-$(Get-Random)"
if (Test-Path $TestRoot) { throw "test root already exists: $TestRoot" }

Write-Output "=== Round trip 1: install / health check / uninstall ==="
$Port = 28420

& $InstallScript -InstallPath $TestRoot -Port $Port
if ($LASTEXITCODE -ne 0) { throw "install.ps1 failed (exit $LASTEXITCODE)" }

$ServerExe = Join-Path $TestRoot "server\sopforge-server.exe"
$CaptureExe = Join-Path $TestRoot "capture\sopforge.exe"
if (-not (Test-Path $ServerExe)) { throw "FAIL: $ServerExe not found after install" }
if (-not (Test-Path $CaptureExe)) { throw "FAIL: $CaptureExe not found after install" }

$SessionsRoot = Join-Path $TestRoot "sessions"
# Explicit stdio redirection is required: this console=False (windowed)
# EXE does not respond at all when launched without it (reproduced
# directly against dist/sopforge-server/sopforge-server.exe while writing
# this script — the same class of bug task-10 found for
# scripts/build_server_exe.py's subprocess launch).
$OutLog = Join-Path $env:TEMP "sopforge-install-test-stdout.log"
$ErrLog = Join-Path $env:TEMP "sopforge-install-test-stderr.log"
$Proc = Start-Process -FilePath $ServerExe `
    -ArgumentList "--port", $Port, "--sessions-root", "`"$SessionsRoot`"" `
    -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog

try {
    if (-not (Wait-ForHealthy -Port $Port)) {
        throw "FAIL: server never responded 200 on port $Port"
    }
    Write-Output "Health check passed."
} finally {
    Stop-ServerCleanly -Proc $Proc -Port $Port
}

& $UninstallScript -InstallPath $TestRoot
if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 failed (exit $LASTEXITCODE)" }

if (Test-Path $TestRoot) {
    throw "FAIL: $TestRoot still exists after uninstall (directory state does not match pre-install baseline)"
}
Write-Output "PASS: install/uninstall round trip -- directory state matches pre-install baseline (absent)."

# --- Round trip 2: -Autostart branch ---
Write-Output ""
Write-Output "=== Round trip 2: -Autostart scheduled task ==="
$TestRoot2 = Join-Path $env:TEMP "sopforge-install-test-autostart-$(Get-Random)"
$TaskName = "SOPForge-Server"
$Port2 = $Port + 1

& $InstallScript -InstallPath $TestRoot2 -Port $Port2 -Autostart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 -Autostart failed (exit $LASTEXITCODE)" }

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    Write-Output "ESCALATE: scheduled task '$TaskName' was not created after -Autostart install."
    Write-Output "This looks like an elevation/policy restriction on ONLOGON-triggered task"
    Write-Output "registration for the current user, not a bug in install.ps1 -- see"
    Write-Output "CLAUDE.md prime directive 1 and record this in phases/DEVIATIONS.md rather"
    Write-Output "than silently skipping the autostart round trip."
    # Best-effort cleanup of the half-completed install before escalating.
    & $UninstallScript -InstallPath $TestRoot2 -RemoveData | Out-Null
    exit 1
}
Write-Output "Autostart scheduled task confirmed via Get-ScheduledTask."

& $UninstallScript -InstallPath $TestRoot2 -RemoveData
if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 (autostart) failed (exit $LASTEXITCODE)" }

$TaskAfter = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($TaskAfter) {
    throw "FAIL: scheduled task '$TaskName' still exists after uninstall"
}
if (Test-Path $TestRoot2) {
    throw "FAIL: $TestRoot2 still exists after uninstall"
}

Write-Output "PASS: -Autostart round trip -- scheduled task removed; directory state matches baseline."
Write-Output ""
Write-Output "ALL PASS"
exit 0
