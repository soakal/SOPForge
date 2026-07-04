<#
.SYNOPSIS
    Automated install/uninstall round-trip (AC4): snapshots directory
    state, installs to a temp path on a non-default port, polls the
    health endpoint, uninstalls, and asserts before/after directory state
    matches. Separately probes the -Autostart branch: creates the
    scheduled task, confirms it via Get-ScheduledTask, then removes it.

.DESCRIPTION
    This was originally going to treat scheduled-task creation failure as
    an escalation-worthy blocker (exit non-zero, per CLAUDE.md prime
    directive 1), and did exactly that the first time it ran here: both
    Register-ScheduledTask and schtasks.exe /create failed with "Access is
    denied" on this build's own VM/account, a genuine Task Scheduler
    permission restriction rather than a bug. That finding was escalated
    to the user, who decided -Autostart should be a documented best-effort
    feature rather than a release blocker (see phases/DEVIATIONS.md's
    "task-12 -Autostart scheduled task" entry for the full history).

    Per that decision, round trip 2 below now treats "the scheduled task
    could not be created on this machine" as a documented SKIP (exit 0),
    not a failure -- while still fully exercising create -> confirm ->
    remove on a machine/account where the restriction is absent. Round
    trip 1 (the core AC4 requirement: install -> health check -> uninstall
    -> directory state matches) is unconditional and always asserted.
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
# Best-effort by design (install.ps1's .DESCRIPTION and
# phases/DEVIATIONS.md's "task-12 -Autostart scheduled task" entry):
# Task Scheduler permission policy can block ONLOGON-triggered task
# registration on some machines/accounts even without elevation. When
# that happens here, install.ps1 itself still succeeds (the scheduled
# task step catches its own failure) -- this round trip verifies the
# scheduled task WHEN it can be created, and treats "could not be
# created on this machine" as a documented, known limitation rather than
# a test failure, per the user's explicit decision to accept -Autostart
# as best-effort rather than block the release on it.
Write-Output ""
Write-Output "=== Round trip 2: -Autostart scheduled task (best-effort) ==="
$TestRoot2 = Join-Path $env:TEMP "sopforge-install-test-autostart-$(Get-Random)"
$TaskName = "SOPForge-Server"
$Port2 = $Port + 1

& $InstallScript -InstallPath $TestRoot2 -Port $Port2 -Autostart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 -Autostart failed (exit $LASTEXITCODE)" }

if (-not (Test-Path (Join-Path $TestRoot2 "server\sopforge-server.exe"))) {
    throw "FAIL: base install (files) did not succeed even though -Autostart is best-effort"
}

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $Task) {
    Write-Output "SKIP: scheduled task '$TaskName' could not be created on this machine/account"
    Write-Output "(Task Scheduler permission restriction, documented in phases/DEVIATIONS.md)."
    Write-Output "install.ps1 itself still succeeded -- this is the accepted best-effort behavior."

    & $UninstallScript -InstallPath $TestRoot2 -RemoveData
    if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 failed while cleaning up the skipped autostart test (exit $LASTEXITCODE)" }
    if (Test-Path $TestRoot2) {
        throw "FAIL: $TestRoot2 still exists after uninstall (cleanup of the skipped autostart test did not complete)"
    }

    Write-Output ""
    Write-Output "ALL PASS (autostart round trip skipped: known environment limitation)"
    exit 0
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
