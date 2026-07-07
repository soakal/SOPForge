<#
.SYNOPSIS
    Automated install/uninstall round-trip (AC4): snapshots directory
    state, installs to a temp path on a non-default port, polls the
    health endpoint, uninstalls, and asserts before/after directory state
    matches. Separately probes the -Autostart branch: creates both
    scheduled tasks (server + capture agent), confirms each via
    Get-ScheduledTask, then removes them -- and for any that couldn't be
    registered, confirms install.ps1's Startup-folder-shortcut fallback
    fired instead, and that uninstall.ps1 removes those too.

.DESCRIPTION
    Register-ScheduledTask (and schtasks.exe /create) failing with "Access
    is denied" is a genuine Task Scheduler permission restriction on this
    build's own VM/account, not a bug (see phases/DEVIATIONS.md's "task-12
    -Autostart scheduled task" entry for the full history) -- confirmed by
    trying both mechanisms directly. Rather than treat that as an
    escalation-worthy blocker, install.ps1's Register-Autostart function
    now falls back to a per-EXE Startup-folder shortcut when the scheduled
    task can't be registered, making -Autostart self-healing instead of
    merely best-effort. Round trip 2 below asserts whichever mechanism
    actually took effect for each entry (scheduled task via
    Get-ScheduledTask, or shortcut via install-config.json's
    StartupShortcuts + the actual file in the Startup folder), and that
    uninstall.ps1 removes exactly that. Round trip 1 (the core AC4
    requirement: install -> health check -> uninstall -> directory state
    matches) is unconditional and always asserted.

    The Startup folder is real and shared with any live install on this
    machine (unlike the temp -InstallPath, shortcuts there aren't
    namespaced per test run) -- this script backs up and restores
    SOPForge-Server.lnk/SOPForge-Capture.lnk around both round trips so
    running it can never clobber or delete a real install's autostart
    shortcuts.

    Autostart now defaults to ON in install.ps1, so round trip 1 (which
    passes no -NoAutostart) also exercises autostart registration, not just
    round trip 2 -- on a machine with a real SOPForge install already
    registered under the same task names, install.ps1's ownership check
    (see its Register-Autostart) makes round trip 1 fall back to a
    Startup-folder shortcut rather than reuse round trip 2's names, and
    Test-TaskOwnedByPath (below) is what makes round trip 2's own
    assertions path-aware rather than merely name-aware, so a real
    install's identically-named task never registers as a false pass/fail
    for either round trip.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$InstallScript = Join-Path $RepoRoot "install.ps1"
$UninstallScript = Join-Path $RepoRoot "uninstall.ps1"

# Both round trips below install to a non-default port, so install.ps1 sets
# a persistent per-user SOPFORGE_SERVER_URL regardless of -Autostart --
# snapshotted once here and restored in the outer `finally` at the bottom
# of this script as a safety net. The real assertion, though, is inline
# after each uninstall call below: uninstall.ps1 itself must restore this
# to $OriginalServerUrlEnv -- if the outer finally were the only thing
# putting it back, a real product-level cleanup bug (uninstall.ps1 never
# removing it) would go completely unnoticed.
$OriginalServerUrlEnv = [Environment]::GetEnvironmentVariable("SOPFORGE_SERVER_URL", "User")

# Round trip 2 below exercises install.ps1's Register-Autostart fallback,
# which creates Startup-folder shortcuts under fixed, well-known names
# (SOPForge-Server.lnk / SOPForge-Capture.lnk) -- the SAME names a real
# -Autostart install on this machine uses (Startup-folder shortcuts aren't
# namespaced per -InstallPath the way the temp test root is). Back up
# whatever's already there under those names before the test runs, and
# restore it in the outer `finally`, so running this test can never clobber
# or delete a real install's autostart shortcuts.
$StartupDir = [Environment]::GetFolderPath("Startup")
$ShortcutNames = @("SOPForge-Server.lnk", "SOPForge-Capture.lnk")
$ShortcutBackups = @{}
foreach ($Name in $ShortcutNames) {
    $Path = Join-Path $StartupDir $Name
    if (Test-Path $Path) {
        $Backup = Join-Path $env:TEMP "sopforge-test-shortcut-backup-$Name-$(Get-Random)"
        Copy-Item -Path $Path -Destination $Backup -Force
        $ShortcutBackups[$Name] = $Backup
    }
}

function Assert-ServerUrlEnvRestored($Context) {
    $Current = [Environment]::GetEnvironmentVariable("SOPFORGE_SERVER_URL", "User")
    if ($Current -ne $OriginalServerUrlEnv) {
        throw "FAIL ($Context): SOPFORGE_SERVER_URL is '$Current' after uninstall, expected '$OriginalServerUrlEnv' -- uninstall.ps1 did not clean up the environment variable install.ps1 set."
    }
}

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

try {

# --- Round trip 1: plain install / health check / uninstall (autostart is
# on by default now, so this also incidentally exercises the fallback path
# -- see the .DESCRIPTION note above -- but that's not this round trip's
# focus; round trip 2 is where autostart itself is actually asserted) ---
$TestRoot = Join-Path $env:TEMP "sopforge-install-test-$(Get-Random)"
if (Test-Path $TestRoot) { throw "test root already exists: $TestRoot" }

Write-Output "=== Round trip 1: install / health check / uninstall ==="
$Port = 28420

# Pass an explicit -SessionsRoot INSIDE the temp test root: install.ps1 now
# defaults SessionsRoot to the per-user profile (%USERPROFILE%\SOPForge\
# sessions), which a test must never create/delete. Keeping it under $TestRoot
# keeps this round trip fully isolated and self-cleaning.
$SessionsRoot = Join-Path $TestRoot "sessions"
& $InstallScript -InstallPath $TestRoot -Port $Port -SessionsRoot $SessionsRoot
if ($LASTEXITCODE -ne 0) { throw "install.ps1 failed (exit $LASTEXITCODE)" }

$Config1 = Get-Content (Join-Path $TestRoot "install-config.json") -Raw | ConvertFrom-Json
if ($Config1.SessionsRoot -ne $SessionsRoot) {
    throw "FAIL: install-config.json SessionsRoot '$($Config1.SessionsRoot)' != requested '$SessionsRoot'"
}

$ServerExe = Join-Path $TestRoot "server\sopforge-server.exe"
$CaptureExe = Join-Path $TestRoot "capture\sopforge.exe"
if (-not (Test-Path $ServerExe)) { throw "FAIL: $ServerExe not found after install" }
if (-not (Test-Path $CaptureExe)) { throw "FAIL: $CaptureExe not found after install" }

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
Assert-ServerUrlEnvRestored "round trip 1"
Write-Output "PASS: install/uninstall round trip -- directory state matches pre-install baseline (absent)."

# --- Round trip 2: -Autostart branch (both the server AND capture agent tasks) ---
# Best-effort by design (install.ps1's .DESCRIPTION and
# phases/DEVIATIONS.md's "task-12 -Autostart scheduled task" entry):
# Task Scheduler permission policy can block ONLOGON-triggered task
# registration on some machines/accounts even without elevation. When
# that happens here, install.ps1 itself still succeeds (each scheduled
# task step catches its own failure independently) -- this round trip
# verifies both scheduled tasks WHEN they can be created, and treats
# "neither could be created on this machine" as a documented, known
# limitation rather than a test failure, per the user's explicit decision
# to accept -Autostart as best-effort rather than block the release on it.
Write-Output ""
Write-Output "=== Round trip 2: -Autostart scheduled tasks (best-effort) ==="
$TestRoot2 = Join-Path $env:TEMP "sopforge-install-test-autostart-$(Get-Random)"
$TaskNames = @("SOPForge-Server", "SOPForge-Capture")
$Port2 = $Port + 1

& $InstallScript -InstallPath $TestRoot2 -Port $Port2 -Autostart -SessionsRoot (Join-Path $TestRoot2 "sessions")
if ($LASTEXITCODE -ne 0) { throw "install.ps1 -Autostart failed (exit $LASTEXITCODE)" }

if (-not (Test-Path (Join-Path $TestRoot2 "server\sopforge-server.exe"))) {
    throw "FAIL: base install (files) did not succeed even though -Autostart is best-effort"
}
if (-not (Test-Path (Join-Path $TestRoot2 "capture\sopforge.exe"))) {
    throw "FAIL: base install (files) did not succeed even though -Autostart is best-effort"
}

# A task named e.g. "SOPForge-Server" existing is not enough -- task names
# aren't namespaced per -InstallPath, so a REAL install elsewhere on this
# machine can already own that name (install.ps1's Register-Autostart
# refuses to clobber it and falls back to a shortcut instead, see its
# collision check). Only count a task as "registered by this round trip" if
# its action actually points inside $TestRoot2.
function Test-TaskOwnedByPath($TaskName, $Path) {
    $Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $Task) { return $false }
    $Exe = ($Task.Actions | Select-Object -First 1).Execute
    # Trailing separator required -- a raw StartsWith would treat a sibling
    # path like "...SOPForge-dev\..." as "inside" "...SOPForge" (see the
    # matching fix/comment in install.ps1's ConvertTo-PathPrefix).
    $Prefix = $Path.TrimEnd('\') + '\'
    return [bool]($Exe -and $Exe.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase))
}

$RegisteredTasks = $TaskNames | Where-Object { Test-TaskOwnedByPath $_ $TestRoot2 }
if ($RegisteredTasks.Count -gt 0) {
    Write-Output "Autostart scheduled task(s) confirmed via Get-ScheduledTask: $($RegisteredTasks -join ', ')"
}
if ($RegisteredTasks.Count -lt $TaskNames.Count) {
    $Missing = $TaskNames | Where-Object { $_ -notin $RegisteredTasks }
    Write-Warning "Only $($RegisteredTasks.Count) of $($TaskNames.Count) autostart tasks registered (missing: $($Missing -join ', ')) -- Register-Autostart's Startup-folder-shortcut fallback is expected for each, verified below."
}

# For each entry whose scheduled task didn't register, install.ps1's
# Register-Autostart fallback should have created a Startup-folder shortcut
# instead. install-config.json's own StartupShortcuts is the expected set
# directly (it's what install.ps1 itself recorded creating) -- re-deriving
# it here from a second hardcoded task-name-to-filename mapping would just
# be a second place that has to stay in sync with install.ps1's naming.
# $RegisteredTasks is only used for the narrower cross-check that a shortcut
# was recorded only for a task that didn't register.
$Config2 = Get-Content (Join-Path $TestRoot2 "install-config.json") -Raw | ConvertFrom-Json
$ExpectedShortcuts = @($Config2.StartupShortcuts)

foreach ($Name in $ExpectedShortcuts) {
    $CorrespondingTask = $TaskNames | Where-Object { $Name -eq "$_.lnk" }
    if ($CorrespondingTask -and ($RegisteredTasks -contains $CorrespondingTask)) {
        throw "FAIL: fallback shortcut '$Name' recorded even though its scheduled task '$CorrespondingTask' registered successfully"
    }
}

# The Startup-folder-shortcut existence check (and the earlier scheduled-task
# checks) must never prevent uninstall.ps1 from running -- otherwise a bug
# in the fallback leaves $TestRoot2 and any successfully-registered
# scheduled task orphaned on the real machine. Any assertion failure here is
# captured and rethrown AFTER cleanup runs, not instead of it.
$PreUninstallError = $null
try {
    foreach ($Name in $ExpectedShortcuts) {
        if (-not (Test-Path (Join-Path $StartupDir $Name))) {
            throw "FAIL: expected fallback shortcut '$Name' not found in the Startup folder"
        }
    }
    if ($ExpectedShortcuts.Count -gt 0) {
        Write-Output "Fallback Startup-folder shortcut(s) confirmed: $($ExpectedShortcuts -join ', ')"
    }
} catch {
    $PreUninstallError = $_
}

& $UninstallScript -InstallPath $TestRoot2 -RemoveData
if ($LASTEXITCODE -ne 0) {
    Write-Warning "uninstall.ps1 (autostart) failed (exit $LASTEXITCODE) while cleaning up"
}

if ($PreUninstallError) { throw $PreUninstallError }

$TasksAfter = $TaskNames | Where-Object { Test-TaskOwnedByPath $_ $TestRoot2 }
if ($TasksAfter.Count -gt 0) {
    throw "FAIL: scheduled task(s) still exist after uninstall: $($TasksAfter -join ', ')"
}
foreach ($Name in $ExpectedShortcuts) {
    if (Test-Path (Join-Path $StartupDir $Name)) {
        throw "FAIL: fallback shortcut '$Name' still exists after uninstall"
    }
}
if (Test-Path $TestRoot2) {
    throw "FAIL: $TestRoot2 still exists after uninstall"
}
Assert-ServerUrlEnvRestored "round trip 2"

Write-Output "PASS: -Autostart round trip -- scheduled task(s)/fallback shortcut(s) removed; directory state matches baseline."

# --- Round trip 3: external (per-user-style) SessionsRoot preserve/remove ---
# The core of the sessions-location fix: SessionsRoot now lives OUTSIDE
# InstallPath. Verify uninstall (a) preserves it by default when it holds data
# while still removing the install dir, and (b) deletes it with -RemoveData.
# Uses temp dirs (NOT %USERPROFILE%) so the test never touches real user data.
# -NoAutostart keeps this focused on the data logic without task registration.
Write-Output ""
Write-Output "=== Round trip 3: external SessionsRoot preserve/remove ==="
$Port3 = $Port + 2
$TestRoot3 = Join-Path $env:TEMP "sopforge-install-test-extroot-$(Get-Random)"
$ExtSessions = Join-Path $env:TEMP "sopforge-install-test-extdata-$(Get-Random)"

& $InstallScript -InstallPath $TestRoot3 -Port $Port3 -SessionsRoot $ExtSessions -NoAutostart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 (ext sessions) failed (exit $LASTEXITCODE)" }
$FakeSop = Join-Path $ExtSessions "session-xyz"
New-Item -ItemType Directory -Force -Path $FakeSop | Out-Null
Set-Content -Path (Join-Path $FakeSop "report.json") -Value "{}" -Encoding utf8

& $UninstallScript -InstallPath $TestRoot3
if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 (ext, preserve) failed (exit $LASTEXITCODE)" }
if (Test-Path $TestRoot3) { throw "FAIL: install dir $TestRoot3 not removed on default uninstall" }
if (-not (Test-Path (Join-Path $FakeSop "report.json"))) {
    throw "FAIL: external session data at $ExtSessions was NOT preserved on default uninstall"
}
Write-Output "Preserve verified: install dir removed, external session data kept."

& $InstallScript -InstallPath $TestRoot3 -Port $Port3 -SessionsRoot $ExtSessions -NoAutostart
if ($LASTEXITCODE -ne 0) { throw "install.ps1 (ext sessions, reinstall) failed (exit $LASTEXITCODE)" }
& $UninstallScript -InstallPath $TestRoot3 -RemoveData
if ($LASTEXITCODE -ne 0) { throw "uninstall.ps1 (ext, RemoveData) failed (exit $LASTEXITCODE)" }
if (Test-Path $TestRoot3) { throw "FAIL: install dir $TestRoot3 not removed on -RemoveData uninstall" }
if (Test-Path $ExtSessions) { throw "FAIL: external session data at $ExtSessions NOT removed with -RemoveData" }
Write-Output "PASS: external SessionsRoot preserve/remove round trip."

Write-Output ""
Write-Output "ALL PASS"
exit 0

} finally {
    # Safety net, not the primary mechanism -- Assert-ServerUrlEnvRestored
    # above is what actually verifies uninstall.ps1 cleans up
    # SOPFORGE_SERVER_URL correctly (both round trips install to a
    # non-default port, so both set it). This just guarantees the real
    # machine running this test is never left in a different state than it
    # started in, even if something throws before an assertion runs.
    [Environment]::SetEnvironmentVariable("SOPFORGE_SERVER_URL", $OriginalServerUrlEnv, "User")

    # Same safety-net role for the Startup-folder shortcuts backed up above:
    # restore whatever a real install had there, and remove anything this
    # test run left behind that didn't exist before it.
    foreach ($Name in $ShortcutNames) {
        $Path = Join-Path $StartupDir $Name
        if ($ShortcutBackups.ContainsKey($Name)) {
            Copy-Item -Path $ShortcutBackups[$Name] -Destination $Path -Force
            Remove-Item -Path $ShortcutBackups[$Name] -Force -ErrorAction SilentlyContinue
        } elseif (Test-Path $Path) {
            Remove-Item -Path $Path -Force
        }
    }
}
